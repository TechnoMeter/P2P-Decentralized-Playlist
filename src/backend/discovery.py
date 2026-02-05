import socket
import threading
import pickle
import sys
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
            # Basic reuse address
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            # SO_REUSEPORT allows multiple processes to bind to the same port.
            # This is critical for running multiple instances on one machine (macOS/Linux).
            if hasattr(socket, 'SO_REUSEPORT'):
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass # Some environments might not support it despite the attribute existing

            try:
                # On some systems, binding to '' or '0.0.0.0' works better for broadcast
                s.bind(('', UDP_PORT))
            except OSError as e:
                self.log(f"Critical Bind Error: {e}. Another instance might be blocking the port.")
                return
            
            while self.running:
                try:
                    data, addr = s.recvfrom(4096)
                    msg = pickle.loads(data)

                    if msg.sender_id == self.node_id:
                        continue # Ignore self
                    
                    if msg.msg_type == 'HELLO':
                        self.log(f"Peer {msg.sender_id} found at {addr[0]}")
                        on_peer_found(msg.sender_id, addr[0], msg.payload.get('tcp_port'))
                except Exception as e:
                    if self.running:
                        self.log(f"Error in listen loop: {e}")

    def broadcast_presence(self):
        """Broadcasts a HELLO message to the subnet."""
        # We use a separate socket for sending to avoid interference with the listener
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            
            msg = Message(
                sender_id=self.node_id,
                sender_ip=self.local_ip,
                msg_type='HELLO',
                payload={'tcp_port': self.tcp_port}
            )
            
            # Broadcast to the entire subnet
            s.sendto(pickle.dumps(msg), ('<broadcast>', UDP_PORT))
            # Also send to localhost explicitly to help local instances find each other
            s.sendto(pickle.dumps(msg), ('127.0.0.1', UDP_PORT))
            self.log("Broadcasted presence to network.")

    def stop(self):
        self.running = False