import sys
import uuid
import time
import threading
import socket
from src.backend.discovery import DiscoveryManager
from src.backend.network_node import NetworkNode
from src.backend.state_manager import StateManager
from src.backend.bully_election import ElectionManager
from src.backend.audio_engine import AudioEngine
from src.utils.config import TCP_PORT, HEARTBEAT_INTERVAL
from src.frontend.app_ui import PlaylistUI
from src.utils.models import Song
from src.utils import config

class CollaborativeNode:
    """Main controller for the Decentralized Playlist."""
    
    def __init__(self, node_id=None):
        self.node_id = node_id or str(uuid.uuid4())[:8]
        self.tcp_port = self._find_available_port(TCP_PORT)
        
        # Initialize UI with the add_song callback
        self.ui = PlaylistUI(self.node_id, self.on_add_song_request)
        
        # --- Backend Initialization ---
        self.state = StateManager(self.node_id, self.ui_log)
        self.state.current_song = None
        self.state.current_song_pos = 0 
        
        # History track list for "Previous" functionality
        self.history = [] 
        
        self.network = NetworkNode(self.node_id, self.state, self.ui_log)
        self.network.port = self.tcp_port
        
        self.election = ElectionManager(self.node_id, self.state, self.network, self.ui_log)
        self.network.election = self.election 
        
        self.discovery = DiscoveryManager(self.node_id, self.tcp_port, self.ui_log)
        self.audio = AudioEngine(self.ui_log)
        self.network.audio = self.audio
        
        # --- Wire UI Callbacks to Controller Methods ---
        self.ui.on_skip_next = self.on_skip_next
        self.ui.on_skip_prev = self.on_skip_prev
        self.ui.on_play_pause = self.on_play_pause
        self.ui.on_seek = self.on_seek
        self.ui.on_shuffle = self.on_shuffle
        self.ui.on_repeat = self.on_repeat
        self.ui.on_clear_queue = self.on_clear_queue
        self.ui.on_remove_song = self.on_remove_song
        self.ui.on_volume_change = self.audio.set_volume
        
        self.last_played_id = None
        self.running = True

    def _find_available_port(self, start_port):
        p = start_port
        while p < start_port + 100:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('', p))
                    return p
            except: p += 1
        return start_port

    def ui_log(self, message):
        formatted_msg = f"[{time.strftime('%H:%M:%S')}] {message}"
        print(formatted_msg) 
        if hasattr(self, 'ui'): self.ui.log_message(formatted_msg)

    # --- Interaction Handlers (Placeholders for Developer) ---
    
    def on_add_song_request(self, file_path):
        title = file_path.replace("\\", "/").split("/")[-1]
        new_song = Song(title=title, added_by=self.node_id, file_path=file_path)
        self.state.add_song(new_song)
        # Propagate to network
        for pid in list(self.network.connections.keys()):
            self.network.send_to_peer(pid, 'QUEUE_SYNC', payload={'song': new_song})

    def on_skip_next(self):
        if not self.election.is_host: return
        self.ui_log("CMD: Skip Next")
        self.audio.stop()
        # Logic to pop next song handled in maintenance loop

    def on_skip_prev(self):
        if not self.election.is_host: return
        self.ui_log("CMD: Skip Previous (Not fully implemented)")
        # TODO: Pop from self.history and insert at front of self.state.playlist

    def on_play_pause(self):
        if not self.election.is_host: return
        self.ui_log("CMD: Play/Pause Toggle")
        # TODO: Implement pause/unpause in AudioEngine and sync state

    def on_seek(self, value):
        if not self.election.is_host: return
        self.ui_log(f"CMD: Seek to {value}%")
        # TODO: Calculate seconds based on total duration and call audio.seek()

    def on_shuffle(self):
        if not self.election.is_host: return
        self.ui_log("CMD: Shuffle Queue")
        # TODO: random.shuffle(self.state.playlist) & send SYNC

    def on_repeat(self):
        if not self.election.is_host: return
        self.ui_log("CMD: Toggle Repeat")

    def on_clear_queue(self):
        if not self.election.is_host: return
        self.ui_log("CMD: Clear Queue")
        self.state.playlist.clear()
        # TODO: Send REMOVE_ALL sync message

    def on_remove_song(self, song_data):
        if not self.election.is_host: return
        self.ui_log(f"CMD: Remove song {song_data}")
        # TODO: Find ID by data, remove from list, sync removal

    # --- Loop & Updates ---

    def _refresh_ui(self):
        if not hasattr(self, 'ui'): return
        
        is_host = self.election.is_host
        leader = self.state.get_host()
        
        # Toggle controls AND update role text with leader ID
        self.ui.set_controls_visible(is_host, host_id=leader)
        
        # Update Playing Info
        cp = self.state.current_song
        self.ui.update_now_playing(cp.title if cp else None, cp.artist if cp else "Unknown")
        
        # Update Playlist (Ideally check for diff before full rebuild)
        self.ui.update_playlist(self.state.playlist)

    def _maintenance_loop(self):
        time.sleep(2) 
        while self.running:
            try:
                self._refresh_ui()
                
                if self.election.is_host:
                    # Heartbeats
                    for pid in list(self.network.connections.keys()):
                        self.network.send_to_peer(pid, 'HEARTBEAT')
                        self.election.update_heartbeat()

                    # Audio Sync
                    if self.audio.is_busy():
                        current_pos = self.audio.get_current_pos()
                        self.state.current_song_pos = current_pos
                        # Update UI slider (rough calc)
                        # self.ui.seek_slider.set(...) 
                        for pid in list(self.network.connections.keys()):
                            self.network.send_to_peer(pid, 'PLAYBACK_SYNC', payload={'pos': current_pos})
                    else:
                        # Auto-play logic
                        target_song = None
                        start_offset = 0
                        
                        if self.state.current_song and self.state.current_song.id != self.last_played_id:
                            target_song = self.state.current_song
                            start_offset = getattr(self.state, 'current_song_pos', 0)
                        elif len(self.state.playlist) > 0:
                            target_song = self.state.playlist.pop(0)
                            self.state.current_song = target_song
                            self.state.current_song_pos = 0
                            
                            # Add to history
                            self.history.append(target_song)
                            
                            for pid in list(self.network.connections.keys()):
                                self.network.send_to_peer(pid, 'NOW_PLAYING', payload={'song': target_song})
                                self.network.send_to_peer(pid, 'REMOVE_SONG', payload={'song_id': target_song.id})
                        elif self.state.current_song is not None:
                            self.ui_log("Playlist complete.")
                            self.state.current_song = None
                            self.state.current_song_pos = 0
                            for pid in list(self.network.connections.keys()):
                                self.network.send_to_peer(pid, 'NOW_PLAYING', payload={'song': None})

                        if target_song:
                            if self.audio.play_song(target_song.file_path, start_time=start_offset):
                                self.last_played_id = target_song.id

                self.election.check_for_host_failure()
                time.sleep(HEARTBEAT_INTERVAL)
            except Exception as e:
                print(f"Error in maintenance loop: {e}")

    def start(self):
        self.network.start_server()
        self.discovery.start_listener(self.on_peer_discovered)
        self.discovery.broadcast_presence()
        threading.Thread(target=self._maintenance_loop, daemon=True).start()
        self.ui_log(f"Node started. {self.state.peers}")
        
        def delayed_election():
            time.sleep(1.0)
            self.ui_log(f"start: ELECTION")
            self.election.start_election()
        threading.Thread(target=delayed_election, daemon=True).start()
        
        self.ui.run()

    def on_peer_discovered(self, pid, ip, port):
        if str(pid) != str(self.node_id):
            self.network.connect_to_peer(pid, ip, port)

if __name__ == "__main__":
    cid = sys.argv[1] if len(sys.argv) > 1 else None
    node = CollaborativeNode(cid)
    node.start()