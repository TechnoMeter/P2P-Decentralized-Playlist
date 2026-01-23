import socket
import threading
import pickle
from src.utils.config import UDP_PORT, get_local_ip
from src.utils.models import Message

class DiscoveryManager:
    """Handles UDP broadcasting and listening for peer discovery."""
    
    def __init__(self, node_id, tcp_port, logger_callback=None):
        self.node_id = node_id
        self.tcp_port = tcp_port
        self.local_ip = get_local_ip()
        self.logger = logger_callback
        self.running = True

    def log(self, text):
        if self.logger:
            self.logger(f"[Discovery] {text}")
        else:
            print(f"[Discovery] {text}")

    def start_listener(self, on_peer_found):
        """Listens for HELLO broadcasts from other peers."""
        thread = threading.Thread(target=self._listen_loop, args=(on_peer_found,), daemon=True)
        thread.start()
        self.log(f"Listener active on {self.local_ip}:{UDP_PORT}")

    def _listen_loop(self, on_peer_found):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('', UDP_PORT))
            
            while self.running:
                try:
                    data, addr = s.recvfrom(4096)
                    msg = pickle.loads(data)
                    
                    # FIXED: Check node_id instead of IP to handle same-machine nodes
                    if msg.sender_id == self.node_id:
                        continue  # Ignore self by node_id, not IP
                    
                    if msg.msg_type == 'HELLO':
                        self.log(f"Peer {msg.sender_id} found at {addr[0]}:{msg.payload.get('tcp_port')}")
                        on_peer_found(msg.sender_id, addr[0], msg.payload.get('tcp_port'))
                except Exception as e:
                    self.log(f"Error in listener: {e}")

    def broadcast_presence(self):
        """Broadcasts a HELLO message to the subnet."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            
            msg = Message(
                sender_id=self.node_id,
                sender_ip=self.local_ip,
                msg_type='HELLO',
                payload={'tcp_port': self.tcp_port}
            )
            
            s.sendto(pickle.dumps(msg), ('<broadcast>', UDP_PORT))
            self.log(f"Broadcasted presence (TCP: {self.tcp_port})")
            
            # Also send to localhost for same-machine testing
            s.sendto(pickle.dumps(msg), ('127.0.0.1', UDP_PORT))

    def stop(self):
        self.running = False