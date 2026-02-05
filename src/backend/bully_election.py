import threading
import time
from src.utils.config import ELECTION_TIMEOUT, HOST_TIMEOUT

# TODO Handle heartbeat/battery based host assignment
class ElectionManager:
    """Implements the Bully Algorithm for Leader Election."""
    
    def __init__(self, node_id, state, network_node, logger_callback=None):
        self.node_id = node_id
        self.network = network_node
        self.logger = logger_callback
        self.state = state
        
        self.leader_id = None
        self.is_election_running = False
        self.received_answer = False
        
        self.init_time = time.time()
        self.last_heartbeat = self.init_time
        self.is_host = False
        
        self.lock = threading.RLock()

    def log(self, text):
        if self.logger: self.logger(f"[Election] {text}")

    def start_election(self):
        """Initiates an election by notifying all higher-ID nodes."""
        with self.lock:
            self.leader_id = self.state.get_host()
            self.log(self.state.peers)
            self.log(f"Starting election. My ID: {self.node_id}")
            self.is_election_running = True
            self.received_answer = False
            
        higher_nodes = [pid for pid in self.network.state.peers.keys() if pid > self.node_id]
        
        if not higher_nodes:
            # I am the highest ID node
            self.declare_victory()
        else:
            for pid in higher_nodes:
                payload={
                    'uptime': self.state.get_uptime()
                }
                self.network.send_to_peer(pid, 'ELECTION', payload=payload)
            
            # Wait for ANSWER messages
            threading.Timer(ELECTION_TIMEOUT, self._check_election_results).start()

    def _check_election_results(self):
        """Checks if any higher-ID node responded during the timeout."""
        with self.lock:
            self.log("Election timeout reached.")
            if not self.received_answer and self.is_election_running:
                self.declare_victory()
            self.is_election_running = False

    def on_election_received(self, sender_id, sender_uptime):
        # Use composite metric: primary = uptime, secondary = node ID
        my_metric = (self.state.get_uptime(), self.node_id)
        sender_metric = (sender_uptime, sender_id)
        self.log(f"Election received from {sender_id}. My metric: {my_metric}, Sender metric: {sender_metric}")
        if sender_metric < my_metric:
            self.network.send_to_peer(sender_id, 'ANSWER')
            self.log(f"Sent ANSWER to {sender_id}")
            # Start own election to propagate
            if not self.is_election_running:
                self.start_election()

    def on_answer_received(self):
        """Called when a higher-ID node acknowledges it is taking over."""
        with self.lock:
            self.received_answer = True
            self.log("Higher-ID node answered. Waiting for coordinator...")

    def declare_victory(self):
        """Declares self as the new Leader/Host."""
        with self.lock:
            self.is_host = True
            self.leader_id = self.node_id
            self.state.set_host(self.node_id)
            self.log("I am the new Host!")
        
        # Notify everyone
        for pid in self.network.connections.keys():
            self.network.send_to_peer(pid, 'COORDINATOR', payload={'leader_id': self.node_id})

    def on_coordinator_received(self, leader_id):
        """Updated when a new coordinator is announced."""
        with self.lock:
            self.leader_id = leader_id
            self.state.set_host(leader_id)
            self.is_host = (leader_id == self.node_id)
            self.is_election_running = False
            self.update_heartbeat()
            self.log(f"New Host: {leader_id}")

    def on_heartbeat_received(self):
        """Resets the failure detection timer."""
        self.update_heartbeat()

    def check_for_host_failure(self):
        """Continuously monitors if the current Host is alive."""
        if not self.is_host and self.leader_id:
            if time.time() - self.last_heartbeat > HOST_TIMEOUT:
                self.log(f"Host {self.leader_id} timed out! Starting election...")
                self.leader_id = None
                if (not self.is_election_running):
                    self.start_election()

    def update_heartbeat(self):
        with self.lock:
            self.last_heartbeat = time.time()
            self.state.update_uptime(int(self.last_heartbeat - self.init_time))
            self.log(f"Updated heartbeat. Uptime: {self.state.uptime} seconds")