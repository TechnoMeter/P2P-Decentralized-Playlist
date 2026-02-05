import threading
import time
from typing import Dict, List, Any
from src.utils.models import Song, Message

class StateManager:
    """
    Manages the distributed state.
    Updated to PERSIST peers (Status: Alive/Offline) and merge updates safely.
    """
    
    def __init__(self, node_id, logger_callback=None):
        self.node_id = node_id
        self.logger = logger_callback
        
        # Local state
        self.playlist: List[Song] = []
        
        # Peer Dict Structure:
        # { 'node_id': {'ip': str, 'port': int, 'status': 'alive', 'uptime': float, ...} }
        self.peers: Dict[str, Dict[str, Any]] = {} 
        self.current_song: Song = None
        self.current_song_pos = 0
        self.current_duration = 0
        
        # 0 = No Repeat, 1 = Repeat All, 2 = Repeat One
        self.repeat_mode = 0 
        
        # Vector Clock: {node_id: counter}
        self.vector_clock: Dict[str, int] = {self.node_id: 0}
        self.pending_messages: List[Message] = []
        
        # My own stats
        self.uptime = 0.0
        self.start_time = time.time()
        self.host_id = None
        
        self.lock = threading.Lock()

    def log(self, text):
        if self.logger: self.logger(f"[State] {text}")

    def increment_clock(self):
        with self.lock:
            self.vector_clock[self.node_id] = self.vector_clock.get(self.node_id, 0) + 1
            return self.vector_clock.copy()

    def update_clock(self, incoming_clock: Dict[str, int]):
        with self.lock:
            for uid, count in incoming_clock.items():
                self.vector_clock[uid] = max(self.vector_clock.get(uid, 0), count)

    def can_process(self, msg: Message) -> bool:
        sender = msg.sender_id
        msg_clock = msg.vector_clock
        if msg_clock.get(sender, 0) != self.vector_clock.get(sender, 0) + 1:
            return False
        for uid, count in msg_clock.items():
            if uid != sender:
                if count > self.vector_clock.get(uid, 0):
                    return False
        return True

    def update_peer(self, node_id, ip, port):
        """Called on discovery or HELLO. Updates connection info while PRESERVING history."""
        with self.lock:
            if node_id in self.peers:
                # PERSISTENCE: Only update connection info and status
                # Do NOT overwrite 'uptime' or 'battery' with defaults here!
                self.peers[node_id]['ip'] = ip
                self.peers[node_id]['port'] = port
                self.peers[node_id]['status'] = 'alive'
                self.peers[node_id]['last_seen'] = time.time()
            else:
                # New user -> Initialize defaults
                self.peers[node_id] = {
                    'ip': ip, 
                    'port': port, 
                    'status': 'alive', 
                    'last_seen': time.time(),
                    'uptime': 0,
                    'battery': 100
                }
            
            if node_id not in self.vector_clock:
                self.vector_clock[node_id] = 0

    def update_peer_heartbeat(self, node_id, uptime, battery):
        """Updates stats from HEARTBEAT messages."""
        with self.lock:
            if node_id in self.peers:
                self.peers[node_id]['status'] = 'alive'
                self.peers[node_id]['last_seen'] = time.time()
                self.peers[node_id]['uptime'] = uptime
                self.peers[node_id]['battery'] = battery

    def mark_peer_offline(self, node_id):
        """Instead of deleting, we mark as offline to persist uptime data."""
        with self.lock:
            if node_id in self.peers:
                self.peers[node_id]['status'] = 'offline'

    def add_song(self, song: Song):
        with self.lock:
            self.playlist.append(song)
            return True
        
    def update_my_uptime(self):
        """Called by main loop to track own uptime."""
        with self.lock:
            self.uptime = time.time() - self.start_time

    def set_restored_uptime(self, saved_uptime):
        """Called when joining a host who remembers us. Shifts start_time back."""
        with self.lock:
            # Shift start_time back so that (now - start_time) equals the saved uptime
            self.start_time = time.time() - saved_uptime
            self.uptime = saved_uptime
            self.log(f"Session Restored! Continued from {saved_uptime:.1f}s uptime.")

    def get_uptime(self):
        with self.lock:
            return self.uptime

    def set_host(self, node_id):
        with self.lock:
            self.host_id = node_id

    def get_host(self):
        with self.lock:
            return self.host_id
        
    def is_host(self, node_id):
        with self.lock:
            return self.host_id == node_id
    
    def get_alive_peers_ids(self):
        with self.lock:
            return [pid for pid, data in self.peers.items() if data['status'] == 'alive']