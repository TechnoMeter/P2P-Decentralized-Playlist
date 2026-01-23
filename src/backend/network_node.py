import socket
import threading
import pickle
import time
from typing import Dict, List
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
        
        # Track state sync and position responses
        self.position_responses: Dict[str, float] = {}  # node_id -> position
        self.state_syncs_received: Dict[str, dict] = {}  # node_id -> state_data
        self.state_sync_timestamp = 0
        
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
                    # Dedicated handler for each peer connection
                    thread = threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True)
                    thread.start()
                except Exception as e:
                    if self.running:
                        self.log(f"Server accept error: {e}")

    def _handle_client(self, conn, addr):
        """Listens for and deserializes incoming Message objects from a peer."""
        peer_id = None
        try:
            while self.running:
                data = conn.recv(BUFFER_SIZE)
                if not data:
                    break # Connection closed by remote node
                
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
                self.log(f"Peer {peer_id or addr[0]} disconnected: {e}")
        finally:
            if peer_id and peer_id in self.connections:
                self.connections.pop(peer_id, None)
            conn.close()

    def connect_to_peer(self, node_id, ip, port):
        """Initiates a persistent TCP connection to a newly discovered peer."""
        node_id = str(node_id)
        
        # GUARD: Prevent redundant or self-connections
        if node_id == self.node_id or node_id in self.connections:
            return

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3.0)
            s.connect((ip, port))
            s.settimeout(None) # Revert to blocking mode
            
            self.connections[node_id] = s
            
            # Send initial WELCOME handshake to identify ourselves
            self.send_to_peer(node_id, 'WELCOME', payload={'id': self.node_id})
            self.log(f"Link established to: {node_id} ({ip}:{port})")
            
            # Start a listener for this specific socket
            thread = threading.Thread(target=self._handle_client, args=(s, (ip, port)), daemon=True)
            thread.start()
        except Exception as e:
            self.log(f"Failed to connect to {node_id} at {ip}:{port} - {e}")

    def send_to_peer(self, node_id, msg_type, payload=None):
        """Sends a Message object, handling Vector Clock logic for causal state sync."""
        if node_id not in self.connections:
            return
        
        # Clone the current clock for the message envelope
        clock = self.state.vector_clock.copy()
        
        # Increment clock for state-altering messages (playlist modifications)
        causal_types = ['QUEUE_SYNC', 'FULL_STATE_SYNC', 'REMOVE_SONG']
        if msg_type in causal_types:
            clock = self.state.increment_clock()
            
        msg = Message(self.node_id, self.ip, msg_type, payload, clock)
        
        try:
            data = pickle.dumps(msg)
            self.connections[node_id].sendall(data)
        except Exception as e:
            self.log(f"Send failure to {node_id}. Closing connection.")
            self.connections.pop(node_id, None)

    def _process_message(self, msg: Message):
        """Validates causal sequence using Vector Clocks and routes messages."""
        sender_id = str(msg.sender_id)
        
        # GUARD: Filter out loopback messages that reached this point
        if sender_id == self.node_id:
            return

        # Quiet logs for frequent heartbeat messages
        if msg.msg_type != 'HEARTBEAT':
            self.log(f"Received {msg.msg_type} from {sender_id}")
        
        # Control messages bypass causal ordering to ensure real-time responsiveness
        bypass_types = [
            'WELCOME', 'HEARTBEAT', 'ELECTION', 'ANSWER', 
            'COORDINATOR', 'REQUEST_STATE', 'NOW_PLAYING', 'PLAYBACK_SYNC',
            'CURRENT_POSITION', 'CURRENT_POSITION_REQUEST'
        ]
        
        if msg.msg_type in bypass_types or self.state.can_process(msg):
            # Update our knowledge of the system-wide event counter
            self.state.update_clock(msg.vector_clock)
            self._handle_logic(msg)
            # Try to release any messages that were waiting for this specific clock tick
            self._check_buffer()
        else:
            self.log(f"Buffering out-of-order {msg.msg_type} from {sender_id}")
            self.state.pending_messages.append(msg)

    def _handle_logic(self, msg: Message):
        """Distributes messages to specific subsystem logic and updates shared state."""
        m_type = msg.msg_type
        
        if m_type == 'WELCOME':
            self.state.update_peer(msg.sender_id, msg.sender_ip, self.port)
            # Newly connected? Ask for the current playlist state immediately
            self.send_to_peer(msg.sender_id, 'REQUEST_STATE')
            
        elif m_type == 'REQUEST_STATE':
            # Peer asked for our playlist; provide current queue and now-playing data
            self.send_to_peer(msg.sender_id, 'FULL_STATE_SYNC', payload={
                'playlist': self.state.playlist,
                'current_song': getattr(self.state, 'current_song', None),
                'current_song_pos': getattr(self.state, 'current_song_pos', 0)
            })

        elif m_type == 'FULL_STATE_SYNC':
            # Initial state synchronization (usually triggered on join)
            incoming = msg.payload.get('playlist', [])
            song = msg.payload.get('current_song')
            pos = msg.payload.get('current_song_pos', 0)
            
            # Store state sync from this peer
            self.state_syncs_received[msg.sender_id] = {
                'playlist': incoming,
                'current_song': song,
                'current_song_pos': pos,
                'timestamp': time.time()
            }
            
            # If we just became host, process the most complete state
            if self.election and self.election.just_became_host:
                self._process_state_syncs_for_new_host()
            else:
                # Regular state sync - take the latest one (based on timestamp)
                latest_sync = self._get_latest_state_sync()
                if latest_sync:
                    self._apply_state_sync(latest_sync)

        elif m_type == 'CURRENT_POSITION_REQUEST':
            # New host is asking for current playback position
            if self.state.current_song:
                current_pos = getattr(self, 'audio', None) and self.audio.get_current_pos()
                if current_pos is None:
                    current_pos = getattr(self.state, 'current_song_pos', 0)
                self.send_to_peer(msg.sender_id, 'CURRENT_POSITION', 
                                 payload={'pos': current_pos, 'song_id': self.state.current_song.id})

        elif m_type == 'CURRENT_POSITION':
            # Received current playback position from a peer
            song_id = msg.payload.get('song_id')
            pos = msg.payload.get('pos', 0)
            
            # Store position response
            self.position_responses[msg.sender_id] = pos
            
            # If we're host and have a current song, update position
            if (self.election and self.election.is_host and 
                self.state.current_song and 
                self.state.current_song.id == song_id):
                
                # Calculate average position from all responses
                avg_pos = self._calculate_average_position()
                if avg_pos > self.state.current_song_pos:
                    self.state.current_song_pos = avg_pos
                    self.log(f"Updated playback position for {self.state.current_song.title}: {avg_pos:.1f}s (from {len(self.position_responses)} peers)")
                    # Clear waiting flag if we were waiting for position
                    if hasattr(self.election.network, 'waiting_for_position'):
                        self.election.network.waiting_for_position = False

        elif m_type in ['ELECTION', 'ANSWER', 'COORDINATOR']:
            if self.election:
                if m_type == 'ELECTION': 
                    self.election.on_election_received(msg.sender_id)
                elif m_type == 'ANSWER': 
                    self.election.on_answer_received()
                elif m_type == 'COORDINATOR': 
                    leader_id = msg.payload['leader_id']
                    self.election.on_coordinator_received(leader_id)
                    
                    # Enhanced leader change handling
                    if leader_id != self.node_id:
                        # We're not the host, stop audio if playing
                        if self.audio:
                            self.audio.stop()
                        # Also clear current song state when we're no longer host
                        if hasattr(self.state, 'current_song'):
                            self.state.current_song = None
                            self.state.current_song_pos = 0
                        # Clear position responses
                        self.position_responses.clear()
                        self.state_syncs_received.clear()
                    else:
                        # We became host! Clear old data and start fresh
                        self.position_responses.clear()
                        self.state_syncs_received.clear()
                        self.state_sync_timestamp = time.time()
            
        elif m_type == 'HEARTBEAT':
            if self.election: 
                self.election.on_heartbeat_received()
            
        elif m_type == 'QUEUE_SYNC':
            # Single song added to the global queue
            song = msg.payload.get('song')
            if song:
                # Check if song already exists in playlist
                song_exists = any(s.id == song.id for s in self.state.playlist)
                if not song_exists:
                    self.state.add_song(song)
                    self.log(f"Queue updated: {song.title} (added by {song.added_by})")
                    
                    # If we're the host, also broadcast to other peers to ensure consistency
                    if self.election and self.election.is_host:
                        for pid in self.connections.keys():
                            if pid != msg.sender_id:  # Don't send back to sender
                                self.send_to_peer(pid, 'QUEUE_SYNC', payload={'song': song})

        elif m_type == 'REMOVE_SONG':
            # Song finished playing or was skipped; remove from upcoming list
            sid = msg.payload.get('song_id')
            # Remove all instances of the song with this ID
            self.state.playlist = [s for s in self.state.playlist if s.id != sid]

        elif m_type == 'NOW_PLAYING':
            # Host notified everyone of a track starting
            song_obj = msg.payload.get('song')
            if song_obj:
                # Set current song
                self.state.current_song = song_obj
                # Update shared metadata for UI
                self.state.now_playing_title = song_obj.title
                # Remove this song from playlist if it's there
                self.state.playlist = [s for s in self.state.playlist if s.id != song_obj.id]
                self.log(f"Host is now playing: {song_obj.title}")
                
                # If we're not the host and have audio, stop any current playback
                if self.election and not self.election.is_host and self.audio:
                    self.audio.stop()
            else:
                # Host cleared now playing
                self.state.current_song = None
                self.state.current_song_pos = 0
                self.log("Host cleared now playing")

        elif m_type == 'PLAYBACK_SYNC':
            # Continuous updates from host about current playback timestamp
            self.state.current_song_pos = msg.payload.get('pos', 0)


    def _process_state_syncs_for_new_host(self):
        """Process multiple state syncs when we become a new host."""
        if not self.state_syncs_received:
            return
        
        # Wait a bit for more responses
        if time.time() - self.state_sync_timestamp < 1.0:
            return
        
        # Get the most complete state (most songs + has current song)
        best_state = None
        best_score = -1
        
        for peer_id, state in self.state_syncs_received.items():
            score = len(state['playlist'])
            if state['current_song']:
                score += 100  # Big bonus for having current song info
            if score > best_score:
                best_score = score
                best_state = state
        
        if best_state:
            self._apply_state_sync(best_state)
            self.log(f"Selected best state from {len(self.state_syncs_received)} peers with {len(best_state['playlist'])} songs")

    def _get_latest_state_sync(self):
        """Get the latest state sync based on timestamp."""
        if not self.state_syncs_received:
            return None
        
        latest_timestamp = 0
        latest_state = None
        
        for peer_id, state in self.state_syncs_received.items():
            if state['timestamp'] > latest_timestamp:
                latest_timestamp = state['timestamp']
                latest_state = state
        
        return latest_state

    def _apply_state_sync(self, state_data):
        """Apply a state sync to our local state."""
        incoming = state_data.get('playlist', [])
        self.state.current_song = state_data.get('current_song')
        self.state.current_song_pos = state_data.get('current_song_pos', 0)
        
        # Clear existing playlist and add incoming songs
        self.state.playlist.clear()
        for s in incoming:
            if not any(local_s.id == s.id for local_s in self.state.playlist):
                self.state.add_song(s)
        
        if self.state.current_song:
            self.log(f"State applied: {self.state.current_song.title} at {self.state.current_song_pos:.1f}s, {len(self.state.playlist)} songs in queue")

    def _calculate_average_position(self):
        """Calculate average position from all received position responses."""
        if not self.position_responses:
            return getattr(self.state, 'current_song_pos', 0)
        
        # Filter out zero positions (they might indicate the peer doesn't have the song)
        valid_positions = [pos for pos in self.position_responses.values() if pos > 0]
        
        if not valid_positions:
            return getattr(self.state, 'current_song_pos', 0)
        
        # Take the maximum position (most advanced) to avoid going backwards
        max_position = max(valid_positions)
        
        # If we have multiple responses, we could average them, but max is safer
        # to avoid going backwards if some peers are lagging
        return max_position

    def _check_buffer(self):
        """Processes buffered messages iteratively as causal gaps are filled."""
        changed = True
        while changed:
            changed = False
            for msg in self.state.pending_messages[:]:
                if self.state.can_process(msg):
                    self.state.update_clock(msg.vector_clock)
                    self._handle_logic(msg)
                    self.state.pending_messages.remove(msg)
                    changed = True