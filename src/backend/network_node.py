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
    Handles TCP connections and message routing. 
    Updated to support Playback Control and Full State Syncs.
    """
    
    def __init__(self, node_id, state_manager, logger_callback=None):
        self.node_id = str(node_id) 
        self.state = state_manager
        self.logger = logger_callback
        self.running = True
        self.port = TCP_PORT 
        self.election = None
        self.audio = None 
        self.connections: Dict[str, socket.socket] = {}
        
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            self.ip = s.getsockname()[0]
            s.close()
        except Exception:
            self.ip = socket.gethostbyname(socket.gethostname())

    def log(self, text):
        if self.logger: self.logger(f"[Network] {text}")

    def start_server(self):
        thread = threading.Thread(target=self._server_loop, daemon=True)
        thread.start()

    def _server_loop(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(('', self.port))
            except Exception as e:
                self.log(f"CRITICAL: Bind failed: {e}")
                return
            s.listen(5)
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
                
                if peer_id == self.node_id:
                    conn.close()
                    return
                
                if peer_id not in self.connections:
                    self.connections[peer_id] = conn
                
                self._process_message(msg)
        except Exception:
            pass      
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
        except Exception:
            pass

    def send_to_peer(self, node_id, msg_type, payload=None):
        if node_id not in self.connections: return
        clock = self.state.vector_clock.copy()
        # Increment clock for state-changing messages
        if msg_type in ['QUEUE_SYNC', 'FULL_STATE_SYNC', 'REMOVE_SONG', 'PLAYBACK_CONTROL']:
            clock = self.state.increment_clock()
        msg = Message(self.node_id, self.ip, msg_type, payload, clock)
        try:
            data = pickle.dumps(msg)
            header = struct.pack('>I', len(data))
            self.connections[node_id].sendall(header + data)
        except:
            self.connections.pop(node_id, None)

    def _process_message(self, msg: Message):
        if str(msg.sender_id) == self.node_id: return
        
        bypass_types = ['HELLO','WELCOME', 'HEARTBEAT', 'ELECTION', 'ANSWER', 'COORDINATOR', 'REQUEST_STATE', 'NOW_PLAYING', 'PLAYBACK_SYNC', 'REMOVE_SONG', 'QUEUE_SYNC', 'FULL_STATE_SYNC', 'PLAYBACK_CONTROL']
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
        elif m_type == 'HEARTBEAT' and self.election:
            self.election.on_heartbeat_received()
        elif m_type == 'HELLO':
            self.state.update_peer(msg.sender_id, msg.sender_ip, self.port)
            if not self.state.get_host() or self.state.is_host(msg.sender_id):
                self.send_to_peer(msg.sender_id, 'REQUEST_STATE')
        
        elif m_type == 'REQUEST_STATE':
            self.send_to_peer(msg.sender_id, 'FULL_STATE_SYNC', payload={
                'playlist': self.state.playlist,
                'current_song': getattr(self.state, 'current_song', None)
            })

        elif m_type == 'FULL_STATE_SYNC':
            # Replaces entire playlist (Good for Shuffle/Clear/Initial Sync)
            self.state.playlist = msg.payload.get('playlist', [])
            self.state.current_song = msg.payload.get('current_song')
            self.log(f"Full state synced. {len(self.state.playlist)} songs in queue.")

        elif m_type in ['ELECTION', 'ANSWER', 'COORDINATOR'] and self.election:
            if m_type == 'ELECTION': self.election.on_election_received(msg.sender_id, msg.payload.get('uptime'))
            elif m_type == 'ANSWER': self.election.on_answer_received()
            elif m_type == 'COORDINATOR': 
                self.election.on_coordinator_received(msg.payload['leader_id'])
                if msg.payload['leader_id'] != self.node_id and self.audio: self.audio.stop()
        
        elif m_type == 'QUEUE_SYNC':
            song = msg.payload.get('song')
            if song:
                with self.state.lock:
                    if not any(s.id == song.id for s in self.state.playlist):
                        self.state.playlist.append(song)
                        self.log(f"Queue updated: {song.title}")

        elif m_type == 'REMOVE_SONG':
            sid = msg.payload.get('song_id')
            self.state.playlist = [s for s in self.state.playlist if s.id != sid]

        elif m_type == 'NOW_PLAYING':
            song_obj = msg.payload.get('song')
            self.state.current_song = song_obj
            if song_obj:
                # Remove from queue if it was there
                self.state.playlist = [s for s in self.state.playlist if s.id != song_obj.id]
        
        elif m_type == 'PLAYBACK_SYNC':
            self.state.current_song_pos = msg.payload.get('pos', 0)
            # Fix: Update duration in state so UI seekbar can sync
            if 'dur' in msg.payload:
                self.state.current_duration = msg.payload['dur']

        elif m_type == 'PLAYBACK_CONTROL':
            action = msg.payload.get('action')
            # Listeners don't actually play audio, but we update UI state if needed
            if action == 'pause':
                self.log("Host paused playback")
            elif action == 'resume':
                self.log("Host resumed playback")

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