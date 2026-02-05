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
    Updated: FULL_STATE_SYNC now includes peer data for persistence restoration.
    """
    
    def __init__(self, node_id, state_manager, logger_callback=None, display_name="Unknown"):
        self.node_id = str(node_id) 
        self.state = state_manager
        self.logger = logger_callback
        self.display_name = display_name 
        self.running = True
        self.port = TCP_PORT 
        self.election = None
        self.audio = None 
        self.connections: Dict[str, socket.socket] = {}
        self.has_restored_persistence = False # Track if we have synced history
        
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
            if peer_id in self.connections: 
                self.connections.pop(peer_id)
            if peer_id:
                # Mark offline instead of deleting to persist data
                self.state.mark_peer_offline(peer_id)
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

            # Send HELLO. If we are new, this triggers WELCOME/Sync from Host.
            self.send_to_peer(node_id, 'HELLO', payload={'id': self.node_id})
            
            threading.Thread(target=self._handle_client, args=(s, (ip, port)), daemon=True).start()
        except Exception:
            pass

    def send_to_peer(self, node_id, msg_type, payload=None):
        if node_id not in self.connections: return
        clock = self.state.vector_clock.copy()
        if msg_type in ['QUEUE_SYNC', 'FULL_STATE_SYNC', 'REMOVE_SONG', 'PLAYBACK_CONTROL']:
            clock = self.state.increment_clock()
        
        # Attach Display Name to every message
        msg = Message(self.node_id, self.ip, msg_type, payload, clock, display_name=self.display_name)
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
        sender = msg.sender_id
        d_name = msg.display_name if msg.display_name else "Unknown"
        
        if m_type == 'HELLO':
            # Update peer with name
            self.state.update_peer(sender, msg.sender_ip, self.port, display_name=d_name)
            # If we are host, welcome them and trigger state sync
            if self.state.is_host(self.node_id) or not self.state.get_host():
                self.send_to_peer(sender, 'WELCOME', payload={'host_id': self.node_id})
                # Auto-send state so they get persistence data immediately
                self.send_to_peer(sender, 'FULL_STATE_SYNC', payload={
                    'playlist': self.state.playlist,
                    'current_song': getattr(self.state, 'current_song', None),
                    'peers': self.state.peers # SEND PEER DATA FOR PERSISTENCE
                })

        elif m_type == 'WELCOME':
            hid = msg.payload.get('host_id')
            if hid: self.state.set_host(hid)
            # Request state just in case, though HELLO usually triggers it
            self.send_to_peer(sender, 'REQUEST_STATE')

        elif m_type == 'HEARTBEAT':
            up = msg.payload.get('uptime', 0)
            bat = msg.payload.get('battery', 100)
            
            # PERSISTENCE PROTECTION:
            # If the incoming heartbeat reports a LOWER uptime than we remember,
            # it means the node crashed and restarted.
            # We should NOT overwrite our high value with their low value yet.
            # Instead, we should trigger a Sync to help them restore their value.
            
            known_uptime = 0
            if sender in self.state.peers:
                known_uptime = self.state.peers[sender].get('uptime', 0)
                            # Update name if changed
                if d_name != "Unknown":
                    self.state.peers[sender]['display_name'] = d_name
            # Allow some drift, but if incoming is significantly less (restart case)
            if up < known_uptime - 10.0:
                # Keep our known high uptime (increment slightly to simulate passage of time)
                # We do NOT accept the reset to 0
                self.state.update_peer_heartbeat(sender, known_uptime + 1.0, bat) 
                
                # Send them the state so they can fix themselves
                # Only Host should do this to prevent message storms
                if self.state.is_host(self.node_id):
                    self.send_to_peer(sender, 'FULL_STATE_SYNC', payload={
                        'playlist': self.state.playlist,
                        'current_song': getattr(self.state, 'current_song', None),
                        'peers': self.state.peers 
                    })
                    self.log(f"Detected restart of {sender} (new={up:.1f} < old={known_uptime:.1f}). Sent Restore Sync.")
            else:
                # Normal update
                self.state.update_peer_heartbeat(sender, up, bat)

            if self.election:
                self.election.on_heartbeat_received()

        elif m_type == 'REQUEST_STATE':
            self.send_to_peer(sender, 'FULL_STATE_SYNC', payload={
                'playlist': self.state.playlist,
                'current_song': getattr(self.state, 'current_song', None),
                'peers': self.state.peers # SEND PEER DATA
            })

        elif m_type == 'FULL_STATE_SYNC':
            self.state.playlist = msg.payload.get('playlist', [])
            self.state.current_song = msg.payload.get('current_song')
            
            # PERSISTENCE RESTORATION LOGIC:
            # Look for our own ID in the incoming peer list (which comes from the Host).
            # If the Host has a record of us with a higher uptime than our current local session,
            # it means we crashed/restarted and the Host "remembers" us.
            # We adopt that memory to continue our uptime score seamlessly.
            incoming_peers = msg.payload.get('peers', {})
            
            if self.node_id in incoming_peers:
                remote_uptime = incoming_peers[self.node_id].get('uptime', 0)
                local_uptime = self.state.get_uptime()
                
                # If remote memory is significantly larger than our fresh start, adopt it.
                if remote_uptime > local_uptime:
                    self.state.set_restored_uptime(remote_uptime)
                    self.log(f"PERSISTENCE RESTORED: Adopted uptime {remote_uptime:.1f}s from Host.")
            
            # Merge other peers (optional, good for mesh knowledge)
            for pid, pdata in incoming_peers.items():
                if pid != self.node_id and pid not in self.state.peers:
                    self.state.peers[pid] = pdata

            self.log(f"Full state synced. {len(self.state.playlist)} songs.")

        elif m_type in ['ELECTION', 'ANSWER', 'COORDINATOR'] and self.election:
            if m_type == 'ELECTION': 
                self.election.on_election_received(sender, msg.payload.get('score', 0))
            elif m_type == 'ANSWER': 
                self.election.on_answer_received()
            elif m_type == 'COORDINATOR': 
                self.election.on_coordinator_received(msg.payload['leader_id'])
                if msg.payload['leader_id'] != self.node_id and self.audio: self.audio.stop()
        
        elif m_type == 'QUEUE_SYNC':
            song = msg.payload.get('song')
            if song:
                with self.state.lock:
                    if not any(s.id == song.id for s in self.state.playlist):
                        self.state.playlist.append(song)

        elif m_type == 'REMOVE_SONG':
            sid = msg.payload.get('song_id')
            self.state.playlist = [s for s in self.state.playlist if s.id != sid]

        elif m_type == 'NOW_PLAYING':
            song_obj = msg.payload.get('song')
            self.state.current_song = song_obj
            if song_obj:
                self.state.playlist = [s for s in self.state.playlist if s.id != song_obj.id]
        
        elif m_type == 'PLAYBACK_SYNC':
            self.state.current_song_pos = msg.payload.get('pos', 0)
            if 'dur' in msg.payload:
                self.state.current_duration = msg.payload['dur']

        elif m_type == 'PLAYBACK_CONTROL':
            action = msg.payload.get('action')
            if action == 'pause': self.log("Host paused playback")
            elif action == 'resume': self.log("Host resumed playback")

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