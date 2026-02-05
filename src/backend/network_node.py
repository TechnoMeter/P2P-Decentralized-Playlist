import socket
import threading
import pickle
import struct
import time
from typing import Dict
from src.utils.config import TCP_PORT, BUFFER_SIZE
from src.utils.models import Message

class NetworkNode:
    """
    The communication backbone of the decentralized playlist.
    Handles TCP connections, message routing, causal ordering via Vector Clocks,
    and coordinates between discovery, election, and audio subsystems.
    """
    
    def __init__(self, node_id, state_manager, logger_callback=None):
        self.node_id = str(node_id) 
        self.state = state_manager
        self.logger = logger_callback
        self.running = True
        
        # This port is dynamically assigned by CollaborativeNode in main.py
        self.port = TCP_PORT 
        
        # Subsystem references (populated by main.py)
        self.election = None
        self.audio = None 
        
        # Active peer connections: {node_id: socket}
        self.connections: Dict[str, socket.socket] = {}
        
        # Resolve local IP address
        try:
            # Connect to a dummy external address to find the primary local interface IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            self.ip = s.getsockname()[0]
            s.close()
        except Exception:
            self.ip = socket.gethostbyname(socket.gethostname())

    def log(self, text):
        """Standardized logging for the network subsystem."""
        if self.logger:
            self.logger(f"[Network] {text}")

    def start_server(self):
        """Starts the background thread to listen for incoming TCP connections."""
        thread = threading.Thread(target=self._server_loop, daemon=True)
        thread.start()

    def _server_loop(self):
        """TCP Server loop to accept connections from other peers on the LAN."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(('', self.port))
            except Exception as e:
                self.log(f"CRITICAL: Bind failed on {self.port}: {e}")
                return
                
            s.listen(5)
            self.log(f"Server listening on {self.port}")
            
            while self.running:
                try:
                    conn, addr = s.accept()
                    threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True).start()
                except: pass

    def _recv_all(self, conn, n):
        data = bytearray()
        while len(data) < n:
            packet = conn.recv(n - len(data))
            if not packet: return None
            data.extend(packet)
        return data

    def _handle_client(self, conn, addr):
        """Listens for and deserializes incoming Message objects from a peer."""
        peer_id = None
        try:
            while self.running:
                header = self._recv_all(conn, 4)
                if not header: break
                msg_len = struct.unpack('>I', header)[0]
                data = self._recv_all(conn, msg_len)
                if not data: break
                
                msg = pickle.loads(data)
                peer_id = str(msg.sender_id)
                
                # GUARD: Ignore any node connecting to itself (loopback)
                if peer_id == self.node_id:
                    conn.close()
                    return
                
                # Register connection mapping if new
                if peer_id not in self.connections:
                    self.connections[peer_id] = conn
                
                self._process_message(msg)
        except Exception as e:
            if self.running:
                self.log(f"Peer {peer_id} disconnected: {e}")
                if self.state.is_host(peer_id) and self.election:
                    self.election.start_election()
                
        finally:
            if peer_id in self.connections: self.connections.pop(peer_id)
            conn.close()

    def connect_to_peer(self, node_id, ip, port):
        node_id = str(node_id)
        if node_id == self.node_id or node_id in self.connections: return
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3.0)
            s.connect((ip, port))
            s.settimeout(None)
            self.connections[node_id] = s
            self.state.update_peer(node_id, ip, port)

            if self.state.is_host(self.node_id):
                self.send_to_peer(node_id, 'WELCOME', payload={'id': self.node_id})

            self.send_to_peer(node_id, 'HELLO', payload={'id': self.node_id})

            threading.Thread(target=self._handle_client, args=(s, (ip, port)), daemon=True).start()
        except Exception as e:
            self.log(f"Connection failed to {node_id}: {e}")

    def send_to_peer(self, node_id, msg_type, payload=None):
        if node_id not in self.connections: return
        clock = self.state.vector_clock.copy()
        if msg_type in ['QUEUE_SYNC', 'FULL_STATE_SYNC', 'REMOVE_SONG']:
            clock = self.state.increment_clock()
        msg = Message(self.node_id, self.ip, msg_type, payload, clock)
        try:
            data = pickle.dumps(msg)
            header = struct.pack('>I', len(data))
            self.connections[node_id].sendall(header + data)
        except:
            self.connections.pop(node_id, None)

    def _process_message(self, msg: Message):
        sender_id = str(msg.sender_id)
        if sender_id == self.node_id: return
        if msg.msg_type not in ['HEARTBEAT', 'PLAYBACK_SYNC']:
            self.log(f"Processing {msg.msg_type} from {sender_id}")
        
        bypass_types = ['HELLO','WELCOME', 'HEARTBEAT', 'ELECTION', 'ANSWER', 'COORDINATOR', 'REQUEST_STATE', 'NOW_PLAYING', 'PLAYBACK_SYNC', 'REMOVE_SONG', 'QUEUE_SYNC']
        if msg.msg_type in bypass_types or self.state.can_process(msg):
            self.state.update_clock(msg.vector_clock)
            self._handle_logic(msg)
            self._check_buffer()
        else:
            self.state.pending_messages.append(msg)

    def _handle_logic(self, msg: Message):
        m_type = msg.msg_type
        if m_type == 'WELCOME': 
            self.state.set_host(msg.sender_id)

        elif m_type == 'HEARTBEAT':
            if self.election:
                self.election.on_heartbeat_received()

        elif m_type == 'HELLO':
            self.state.update_peer(msg.sender_id, msg.sender_ip, self.port)
            # Only request state if we don't have a host or if this is the host
            if not self.state.get_host() or self.state.is_host(msg.sender_id):
                self.send_to_peer(msg.sender_id, 'REQUEST_STATE')

        elif m_type == 'REQUEST_STATE':
            self.send_to_peer(msg.sender_id, 'FULL_STATE_SYNC', payload={
                'playlist': self.state.playlist,
                'current_song': getattr(self.state, 'current_song', None)
            })

        elif m_type == 'FULL_STATE_SYNC':
            incoming = msg.payload.get('playlist', [])
            self.state.current_song = msg.payload.get('current_song')
            # ATOMIC MERGE: Use the StateManager lock to prevent duplicates during concurrent syncs
            with self.state.lock:
                for s in incoming:
                    # Check both ID AND Title to catch same-file-different-owner issues
                    is_duplicate = any(local_s.id == s.id for local_s in self.state.playlist)
                    if not is_duplicate:
                        self.state.playlist.append(s)
                        self.log(f"Synced song: {s.title}")

        elif m_type in ['ELECTION', 'ANSWER', 'COORDINATOR']:
            if self.election:
                if m_type == 'ELECTION': 
                    self.election.on_election_received(msg.sender_id, msg.payload.get('uptime'))
                elif m_type == 'ANSWER': self.election.on_answer_received()
                elif m_type == 'COORDINATOR': 
                    leader_id = msg.payload['leader_id']
                    self.election.on_coordinator_received(leader_id)
                    if leader_id != self.node_id and self.audio: self.audio.stop()
        
        elif m_type == 'QUEUE_SYNC':
            song = msg.payload.get('song')
            if song:
                with self.state.lock:
                    if not any(s.id == song.id for s in self.state.playlist):
                        self.state.playlist.append(song)
                        self.log(f"Queue updated: {song.title}, {song.id}")

        elif m_type == 'REMOVE_SONG':
            sid = msg.payload.get('song_id')
            self.state.playlist = [s for s in self.state.playlist if s.id != sid]

        elif m_type == 'NOW_PLAYING':
            song_obj = msg.payload.get('song')
            self.state.current_song = song_obj
            if song_obj:
                self.state.now_playing_title = song_obj.title
                self.state.playlist = [s for s in self.state.playlist if s.id != song_obj.id]
        
        elif m_type == 'PLAYBACK_SYNC':
            self.state.current_song_pos = msg.payload.get('pos', 0)

    def _check_buffer(self):
        changed = True
        while changed:
            changed = False
            for msg in self.state.pending_messages[:]:
                if self.state.can_process(msg):
                    self.state.update_clock(msg.vector_clock)
                    self._handle_logic(msg)
                    self.state.pending_messages.remove(msg)
                    changed = True