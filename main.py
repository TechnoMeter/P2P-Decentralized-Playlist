import sys
import uuid
import time
import threading
import socket
import random
import pygame # Needed for length check
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
        
        self.ui = PlaylistUI(self.node_id, self.on_add_song_request)
        
        self.state = StateManager(self.node_id, self.ui_log)
        self.state.current_duration = 0 # Track total duration in state
        self.history = [] 
        
        self.network = NetworkNode(self.node_id, self.state, self.ui_log)
        self.network.port = self.tcp_port
        
        self.election = ElectionManager(self.node_id, self.state, self.network, self.ui_log)
        self.network.election = self.election 
        
        self.discovery = DiscoveryManager(self.node_id, self.tcp_port, self.ui_log)
        self.audio = AudioEngine(self.ui_log)
        self.network.audio = self.audio
        
        # Track shuffle state locally (backend shuffle is just randomizing the list, but we track intent for UI)
        self.is_shuffle_active = False 
        
        # Wire UI Callbacks
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

    # --- Interaction Handlers ---
    
    def on_add_song_request(self, file_path):
        title = file_path.replace("\\", "/").split("/")[-1]
        new_song = Song(title=title, added_by=self.node_id, file_path=file_path)
        self.state.add_song(new_song)
        self._broadcast('QUEUE_SYNC', {'song': new_song})

    def on_skip_next(self):
        if not self.election.is_host: return
        self.ui_log("CMD: Skip Next")
        self.audio.stop()
        # Reset last_played_id so loop picks up new song immediately
        self.last_played_id = None 

    def on_skip_prev(self):
        if not self.election.is_host: return
        self.ui_log("CMD: Skip Previous")
        
        # Logic: If > 5 seconds in, restart song. Else go to previous.
        if self.state.current_song_pos > 5.0:
            self.audio.seek(0)
            self.state.current_song_pos = 0
            # Broadcast state duration
            self._broadcast('PLAYBACK_SYNC', {'pos': 0, 'dur': getattr(self.state, 'current_duration', 0)})
            return

        if self.history:
            prev_song = self.history.pop()
            # Push current song back to front of queue if it exists
            if self.state.current_song:
                self.state.playlist.insert(0, self.state.current_song)
            
            self.state.current_song = prev_song
            self.state.current_song_pos = 0
            self.last_played_id = None # Force play
            
            # Update state manually here to feel responsive
            self._play_song_logic(prev_song)

    def on_play_pause(self):
        if not self.election.is_host: return
        is_playing = self.audio.toggle_pause()
        action = 'resume' if is_playing else 'pause'
        self.ui_log(f"CMD: {action.upper()}")
        self._broadcast('PLAYBACK_CONTROL', {'action': action})
        self.ui.update_play_pause_icon(is_playing)

    def on_seek(self, value):
        if not self.election.is_host: return
        # value is percentage 0-100
        dur = getattr(self.state, 'current_duration', 0)
        if dur > 0:
            seek_sec = (float(value) / 100.0) * dur
            self.audio.seek(seek_sec)
            self.state.current_song_pos = seek_sec
            self._broadcast('PLAYBACK_SYNC', {'pos': seek_sec, 'dur': dur})

    def on_shuffle(self):
        if not self.election.is_host: return
        self.is_shuffle_active = not self.is_shuffle_active
        self.ui_log(f"CMD: Shuffle {'ON' if self.is_shuffle_active else 'OFF'}")
        
        if self.is_shuffle_active:
            random.shuffle(self.state.playlist)
            self._broadcast('FULL_STATE_SYNC', {'playlist': self.state.playlist, 'current_song': self.state.current_song})
        
        self.ui.update_toggles(self.state.repeat_mode, self.is_shuffle_active)

    def on_repeat(self):
        if not self.election.is_host: return
        # Cycle: 0(Off) -> 1(All) -> 2(One) -> 0
        self.state.repeat_mode = (self.state.repeat_mode + 1) % 3
        modes = ["Off", "Repeat All", "Repeat One"]
        self.ui_log(f"CMD: Repeat Mode set to {modes[self.state.repeat_mode]}")
        self.ui.update_toggles(self.state.repeat_mode, self.is_shuffle_active)

    def on_clear_queue(self):
        if not self.election.is_host: return
        self.ui_log("CMD: Clear Queue")
        self.state.playlist.clear()
        self._broadcast('FULL_STATE_SYNC', {'playlist': [], 'current_song': self.state.current_song})

    def on_remove_song(self, song_data):
        if not self.election.is_host: return
        target_id = None
        for s in self.state.playlist:
            if s.title == song_data[0] and s.artist == song_data[1] and s.added_by == song_data[2]:
                target_id = s.id
                break
        if target_id:
            self.state.playlist = [s for s in self.state.playlist if s.id != target_id]
            self.ui_log(f"Removed: {song_data[0]}")
            self._broadcast('REMOVE_SONG', {'song_id': target_id})

    def _broadcast(self, msg_type, payload):
        for pid in list(self.network.connections.keys()):
            self.network.send_to_peer(pid, msg_type, payload=payload)

    # --- Helpers ---

    def _get_duration(self, file_path):
        try:
            return pygame.mixer.Sound(file_path).get_length()
        except:
            return 180.0 # Fallback 3 mins

    def _play_song_logic(self, song, start_offset=0):
        """Centralized play logic for both loop and manual calls."""
        if self.audio.play_song(song.file_path, start_time=start_offset):
            self.last_played_id = song.id
            # Set shared state duration
            self.state.current_duration = self._get_duration(song.file_path)
            
            # Sync everything to peers
            self._broadcast('NOW_PLAYING', {'song': song})
            self._broadcast('PLAYBACK_SYNC', {'pos': start_offset, 'dur': self.state.current_duration})
            
            # If we just played a song from queue, update the queue for everyone
            self._broadcast('FULL_STATE_SYNC', {'playlist': self.state.playlist, 'current_song': song})
            
            # Update Host UI immediately
            self.ui.update_play_pause_icon(True)

    # --- Loop & Updates ---

    def _refresh_ui(self):
        if not hasattr(self, 'ui'): return
        
        is_host = self.election.is_host
        leader = self.state.get_host()
        
        self.ui.set_controls_visible(is_host, host_id=leader)
        
        cp = self.state.current_song
        self.ui.update_now_playing(cp.title if cp else None, cp.artist if cp else "Unknown")
        self.ui.update_playlist(self.state.playlist)
        
        # Update progress and toggles
        # Use state duration so it works for both host and peers
        self.ui.update_progress(self.state.current_song_pos, getattr(self.state, 'current_duration', 0))

        self.ui.update_toggles(self.state.repeat_mode, self.is_shuffle_active)

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

                    # Audio Logic
                    if self.audio.is_busy() or (self.audio.is_paused):
                        current_pos = self.audio.get_current_pos()
                        self.state.current_song_pos = current_pos
                        # Broadcast current duration so peers can sync seekbar
                        self._broadcast('PLAYBACK_SYNC', {'pos': current_pos, 'dur': getattr(self.state, 'current_duration', 0)})
                    else:
                        # Song Finished or Not Started
                        target_song = None
                        start_offset = 0

                        # FIX: Check if we need to resume a song that State says is playing 
                        # but Audio Engine hasn't started yet (Host Migration scenario)
                        if self.state.current_song and self.state.current_song.id != self.last_played_id:
                            target_song = self.state.current_song
                            start_offset = self.state.current_song_pos
                        
                        # 1. Check Repeat One
                        elif self.state.repeat_mode == 2 and self.state.current_song:
                            target_song = self.state.current_song
                            
                        # 2. Check Next in Queue
                        elif len(self.state.playlist) > 0:
                            # If Repeat All is on, add current back to end before popping next
                            if self.state.repeat_mode == 1 and self.state.current_song:
                                self.state.playlist.append(self.state.current_song)
                                
                            target_song = self.state.playlist.pop(0)
                            
                            # Add to history
                            if self.state.current_song and self.state.current_song.id != target_song.id:
                                self.history.append(self.state.current_song)
                            
                            self.state.current_song = target_song
                            self.state.current_song_pos = 0
                            
                        # 3. Playlist Ended
                        elif self.state.current_song is not None:
                            self.ui_log("Playlist complete.")
                            self.state.current_song = None
                            self._broadcast('NOW_PLAYING', {'song': None})

                        if target_song:
                            self._play_song_logic(target_song, start_offset)

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