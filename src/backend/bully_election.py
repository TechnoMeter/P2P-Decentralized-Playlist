import threading
import time
from src.utils.config import ELECTION_TIMEOUT, HOST_TIMEOUT

class ElectionManager:
    """
    Custom Score-Based Election (Modified Bully).
    Score = Uptime * BatteryPenalty.
    Higher score wins.
    """
    
    def __init__(self, node_id, state, network_node, logger_callback=None, battery_callback=None):
        self.node_id = node_id
        self.network = network_node
        self.logger = logger_callback
        self.state = state
        self.get_battery = battery_callback # Function to get current battery
        
        self.leader_id = None
        self.is_election_running = False
        self.received_answer = False
        
        self.last_heartbeat = time.time()
        self.is_host = False
        
        self.lock = threading.RLock()

    def log(self, text):
        if self.logger: self.logger(f"[Election] {text}")

    def calculate_my_score(self):
        """
        Logic: Uptime is the base score.
        Battery < 20% -> 50% Penalty
        Battery < 50% -> 20% Penalty
        """
        uptime = self.state.get_uptime()
        battery = self.get_battery() if self.get_battery else 100
        
        penalty = 0.0
        if battery < 20:
            penalty = 0.5
        elif battery < 50:
            penalty = 0.2
            
        score = uptime * (1.0 - penalty)
        return score

    def start_election(self):
        """Broadcasts ELECTION message with MY SCORE to all alive peers."""
        with self.lock:
            self.is_election_running = True
            self.received_answer = False
            
            my_score = self.calculate_my_score()
            self.log(f"Starting Election. My Score: {my_score:.1f} (Up: {self.state.get_uptime():.0f}s, Bat: {self.get_battery()}%)")
            
            # Send to ALL alive peers (not just higher IDs, because ID doesn't matter now)
            alive_peers = self.state.get_alive_peers_ids()
            
            if not alive_peers:
                # No peers? I win.
                self._declare_victory()
                return

            for pid in alive_peers:
                self.network.send_to_peer(pid, 'ELECTION', payload={'score': my_score})
            
            # Wait for answers
            threading.Timer(ELECTION_TIMEOUT, self._check_election_result).start()

    def on_election_received(self, sender_id, sender_score):
        """
        If my score is HIGHER than sender's, I bully them (send ANSWER) and take over.
        """
        my_score = self.calculate_my_score()
        
        if my_score > sender_score:
            # I am better. Veto.
            self.network.send_to_peer(sender_id, 'ANSWER')
            # Ensure I am running for election too
            if not self.is_election_running:
                self.start_election()
        else:
            # They are better. I yield.
            pass

    def on_answer_received(self):
        """Someone with a better score responded. I step down."""
        with self.lock:
            self.received_answer = True
            # self.log("Received ANSWER (Higher score exists). Waiting for Coordinator msg.")

    def _check_election_result(self):
        """Called after timeout. If no one better answered, I win."""
        with self.lock:
            if self.is_election_running and not self.received_answer:
                self._declare_victory()

    def _declare_victory(self):
        with self.lock:
            self.is_host = True
            self.leader_id = self.node_id
            self.state.set_host(self.node_id)
            self.is_election_running = False
            self.log(f"I won! Score: {self.calculate_my_score():.1f}")
            
            # Announce to all with score for conflict resolution
            my_score = self.calculate_my_score()
            for pid in self.network.connections.keys():
                self.network.send_to_peer(pid, 'COORDINATOR', payload={
                    'leader_id': self.node_id,
                    'score': my_score
                })

    def on_coordinator_received(self, leader_id, sender_score=None):
        with self.lock:
            # Conflict resolution: if we think we're host and someone else claims to be
            if self.is_host and leader_id != self.node_id:
                my_score = self.calculate_my_score()
                other_score = sender_score if sender_score is not None else 0

                if my_score > other_score:
                    # I have higher score, re-assert my leadership
                    self.log(f"Conflict: I have higher score ({my_score:.1f} > {other_score:.1f}), staying host")
                    # Send COORDINATOR back to assert dominance
                    self.network.send_to_peer(leader_id, 'COORDINATOR', payload={
                        'leader_id': self.node_id,
                        'score': my_score
                    })
                    return
                else:
                    # They have higher or equal score, step down
                    self.log(f"Conflict: Stepping down ({my_score:.1f} <= {other_score:.1f})")

            self.leader_id = leader_id
            self.state.set_host(leader_id)
            self.is_host = (leader_id == self.node_id)
            self.is_election_running = False
            self.update_heartbeat()
            self.log(f"New Host: {leader_id}")

    def on_heartbeat_received(self):
        self.update_heartbeat()

    def check_for_host_failure(self):
        """Runs in loop. Checks if leader has timed out."""
        if not self.is_host and self.leader_id:
            # Check last heartbeat time
            if time.time() - self.last_heartbeat > HOST_TIMEOUT:
                self.log(f"Host {self.leader_id} timed out! Starting election...")
                self.leader_id = None
                self.state.mark_peer_offline(self.leader_id) # Mark old host offline
                if not self.is_election_running:
                    self.start_election()

    def update_heartbeat(self):
        with self.lock:
            self.last_heartbeat = time.time()