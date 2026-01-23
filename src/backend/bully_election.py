import threading
import time
from src.utils.config import ELECTION_TIMEOUT, HOST_TIMEOUT

class ElectionManager:
    """Implements the Bully Algorithm for Leader Election."""
    
    def __init__(self, node_id, network_node, logger_callback=None):
        self.node_id = node_id
        self.network = network_node
        self.logger = logger_callback
        
        self.leader_id = None
        self.is_election_running = False
        self.received_answer = False
        
        self.last_heartbeat = time.time()
        self.is_host = False
        self.just_became_host = False
        
        self.lock = threading.Lock()

    def log(self, text):
        if self.logger: self.logger(f"[Election] {text}")

    def start_election(self):
        """Initiates an election by notifying all higher-ID nodes."""
        # Don't start election if we're already host
        if self.is_host:
            return
            
        with self.lock:
            self.log(f"Starting election. My ID: {self.node_id}")
            self.is_election_running = True
            self.received_answer = False
            self.is_host = False
            self.leader_id = None
            self.just_became_host = False
            
        higher_nodes = [pid for pid in self.network.state.peers.keys() if pid > self.node_id]
        
        for pid in list(self.network.connections.keys()):
            if pid > self.node_id and pid not in higher_nodes:
                higher_nodes.append(pid)
        
        self.log(f"Higher nodes to contact: {higher_nodes}")
        
        if not higher_nodes:
            self.log("No higher nodes found. Declaring victory.")
            self.declare_victory()
        else:
            for pid in higher_nodes:
                self.log(f"Sending ELECTION to {pid}")
                self.network.send_to_peer(pid, 'ELECTION')
            
            # Wait for ANSWER messages
            threading.Timer(ELECTION_TIMEOUT, self._check_election_results).start()

    def _check_election_results(self):
        """Checks if any higher-ID node responded during the timeout."""
        with self.lock:
            if not self.received_answer and self.is_election_running:
                # No one answered, so I should be the leader
                self.log("No answers received. I am the new host.")
                self.declare_victory()
            elif self.received_answer and self.is_election_running:
                # We got answers, but no coordinator yet - this is the problematic case
                # We should wait longer or check if we have a leader
                self.log(f"Received answers but no coordinator yet. Current leader: {self.leader_id}")
                if self.leader_id:
                    self.log(f"Already have a leader: {self.leader_id}. Stopping election.")
                else:
                    self.log("Still waiting for coordinator...")
                    # Wait a bit more for coordinator
                    threading.Timer(1.0, self._check_coordinator_timeout).start()
            self.is_election_running = False

    def _check_coordinator_timeout(self):
        """Called if we still haven't received a coordinator after extra wait."""
        with self.lock:
            if not self.leader_id and not self.is_host:
                self.log("Still no coordinator after extra wait. Starting new election.")
                threading.Thread(target=self.start_election).start()

    def on_election_received(self, sender_id):
        """Responds to an election request from a lower-ID node."""
        if sender_id < self.node_id:
            self.log(f"Received ELECTION from lower node {sender_id}. Sending ANSWER.")
            self.network.send_to_peer(sender_id, 'ANSWER')
            
            # If we're already the host, immediately send COORDINATOR
            if self.is_host:
                self.log(f"I am already host. Sending COORDINATOR to {sender_id}.")
                self.network.send_to_peer(sender_id, 'COORDINATOR', payload={'leader_id': self.node_id})
            elif not self.is_election_running:
                # Start our own election
                self.log("Starting election in response to lower node.")
                self.start_election()

    def on_answer_received(self):
        """Called when a higher-ID node acknowledges it is taking over."""
        with self.lock:
            self.received_answer = True
            self.log("Higher-ID node answered.")

    def declare_victory(self):
        """Declares self as the new Leader/Host."""
        with self.lock:
            self.is_host = True
            self.leader_id = self.node_id
            self.is_election_running = False
            self.just_became_host = True
            self.log(f"I am the new Host! (ID: {self.node_id})")
        
        # Request state from all peers to ensure we have the latest playlist
        for pid in self.network.connections.keys():
            self.network.send_to_peer(pid, 'REQUEST_STATE')
        
        # Notify all connected peers
        for pid in self.network.connections.keys():
            self.network.send_to_peer(pid, 'COORDINATOR', payload={'leader_id': self.node_id})

    def on_coordinator_received(self, leader_id):
        """Updated when a new coordinator is announced."""
        with self.lock:
            self.leader_id = leader_id
            old_host_status = self.is_host
            self.is_host = (leader_id == self.node_id)
            self.is_election_running = False
            self.last_heartbeat = time.time()
            
            if leader_id == self.node_id:
                self.just_became_host = True
                self.log(f"I am now the Host!")
            else:
                self.just_became_host = False
                self.log(f"New Host elected: {leader_id}")

    def on_heartbeat_received(self):
        """Resets the failure detection timer."""
        self.last_heartbeat = time.time()

    def check_for_host_failure(self):
        """Continuously monitors if the current Host is alive."""
        if not self.is_host and self.leader_id:
            if time.time() - self.last_heartbeat > HOST_TIMEOUT:
                self.log(f"Host {self.leader_id} timed out! Starting election...")
                self.leader_id = None
                self.start_election()