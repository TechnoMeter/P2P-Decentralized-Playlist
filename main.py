import sys
import uuid
import time
import threading
import socket
import random
import pygame
import os
import hashlib

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

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
    
    def __init__(self, display_name=None, password=None):
        if not display_name or not password:
            print("ERROR: Name and Password are required.")
            print("Usage: python main.py [Name] [Password]")
            sys.exit(1)

        seed = f"{display_name}:{password}"
        full_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, seed)
        self.node_id = str(full_uuid)[:8]
        self.display_name = display_name

        self.tcp_port = self._find_available_port(TCP_PORT)
        
        self.ui = PlaylistUI(f"{self.display_name} [{self.node_id}]", self.on_add_song_request)
        
        self.state = StateManager(self.node_id, self.ui_log)
        self.state.current_duration = 0 
        self.history = [] 
        
        self.network = NetworkNode(self.node_id, self.state, self.ui_log, display_name=self.display_name)
        self.network.port = self.tcp_port
        
        self.election = ElectionManager(
            self.node_id, 
            self.state, 
            self.network, 
            self.ui_log,
            battery_callback=self._get_battery_level
        )
        self.network.election = self.election 
        
        self.discovery = DiscoveryManager(self.node_id, self.tcp_port, self.ui_log, display_name=self.display_name)
        self.audio = AudioEngine(self.ui_log)
        self.network.audio = self.audio
        
        self.is_shuffle_active = False 
        self.last_played_id = None
        self.local_is_paused = False
        self.running = True
        
        self.simulated_battery = 100
        
        self.ui.on_skip_next = self.on_skip_next
        self.ui.on_skip_prev = self.on_skip_prev
        self.ui.on_play_pause = self.on_play_pause
        self.ui.on_seek = self.on_seek
        self.ui.on_shuffle = self.on_shuffle
        self.ui.on_repeat = self.on_repeat
        self.ui.on_clear_queue = self.on_clear_queue
        self.ui.on_remove_song = self.on_remove_song
        self.ui.on_volume_change = self.on_volume_change

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

    def _get_battery_level(self):
        if HAS_PSUTIL:
            battery = psutil.sensors_battery()
            if battery:
                return int(battery.percent)
        return int(self.simulated_battery)

    def on_add_song_request(self, file_path):
        title = file_path.replace("\\", "/").split("/")[-1]
        new_song = Song(title=title, added_by=self.display_name, file_path=file_path)
        self.state.add_song(new_song)
        self._broadcast('QUEUE_SYNC', {'song': new_song})

    def on_skip_next(self):
        if not self.election.is_host: return
        self.ui_log("CMD: Skip Next")
        self.audio.stop()
        self.local_is_paused = False
        if len(self.state.playlist) > 0:
            if self.state.repeat_mode == 1 and self.state.current_song:
                self.state.playlist.append(self.state.current_song)
            target_song = self.state.playlist.pop(0)
            if self.state.current_song:
                self.history.append(self.state.current_song)
            self.state.current_song = target_song
            self.state.current_song_pos = 0
            self.last_played_id = None 
            self._play_song_logic(target_song)
        else:
            self.ui_log("Queue end.")
            self.state.current_song = None
            self._broadcast('NOW_PLAYING', {'song': None})
            self.last_played_id = None

    def on_skip_prev(self):
        if not self.election.is_host: return
        self.ui_log("CMD: Skip Previous")
        if self.state.current_song_pos > 5.0:
            self.audio.seek(0)
            self.state.current_song_pos = 0
            self._broadcast('PLAYBACK_SYNC', {'pos': 0, 'dur': getattr(self.state, 'current_duration', 0)})
            return
        if self.history:
            prev_song = self.history.pop()
            if self.state.current_song:
                self.state.playlist.insert(0, self.state.current_song)
            self.state.current_song = prev_song
            self.state.current_song_pos = 0
            self.last_played_id = None 
            self.local_is_paused = False
            self._play_song_logic(prev_song)

    def on_play_pause(self):
        if not self.election.is_host: return
        if not self.state.current_song: return
        is_playing = self.audio.toggle_pause()
        self.local_is_paused = not is_playing 
        action = 'resume' if is_playing else 'pause'
        self.ui_log(f"CMD: {action.upper()}")
        self._broadcast('PLAYBACK_CONTROL', {'action': action})
        self.ui.update_play_pause_icon(is_playing)

    def on_seek(self, value):
        if not self.election.is_host: return
        dur = getattr(self.state, 'current_duration', 0)
        if dur > 0:
            seek_sec = (float(value) / 100.0) * dur
            self.audio.seek(seek_sec)
            self.state.current_song_pos = seek_sec
            self._broadcast('PLAYBACK_SYNC', {'pos': seek_sec, 'dur': dur})
            
    def on_volume_change(self, val):
        self.audio.set_volume(val)

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

    def _get_duration(self, file_path):
        try:
            return pygame.mixer.Sound(file_path).get_length()
        except:
            return 180.0 

    def _resolve_local_path(self, remote_path):
        """
        Smart Path Resolver for Cross-Platform compatibility.
        1. Checks exact path.
        2. Checks 'assets/filename'.
        3. Checks 'assets/music/filename'.
        4. Checks current directory.
        5. Walks 'assets' directory to find filename.
        """
        if not remote_path: return None
        
        # 1. Exact path check
        if os.path.exists(remote_path):
            return remote_path
            
        filename = remote_path.replace('\\', '/').split('/')[-1]
        
        # Define search candidates
        candidates = [
            os.path.join("assets", filename),
            os.path.join("assets", "music", filename),
            os.path.join("src", "assets", "music", filename), # In case running from root
            filename
        ]
        
        for path in candidates:
            if os.path.exists(path):
                return path

        # Deep search in assets folder as fallback
        if os.path.exists("assets"):
            for root, dirs, files in os.walk("assets"):
                if filename in files:
                    return os.path.join(root, filename)
            
        return None

    def _play_song_logic(self, song, start_offset=0):
        # Resolve the actual file path locally
        local_path = self._resolve_local_path(song.file_path)

        if not local_path:
            self.ui_log(f"Error: File missing locally: {song.file_path}")
            self.ui.show_notification(f"Missing File: {song.title}", is_error=True)
            
            if self.election.is_host:
                self.ui_log("Host missing file. Skipping to next...")
                self.last_played_id = song.id 
                
                if len(self.state.playlist) > 0:
                    next_song = self.state.playlist.pop(0)
                    self.state.current_song = next_song
                    self.state.current_song_pos = 0
                    # Use recursive call to resolve path for next song too
                    self._play_song_logic(next_song)
                else:
                    self.state.current_song = None 
                    self._broadcast('NOW_PLAYING', {'song': None}) 
                    self.ui_log("Queue ended (last song missing).")
                return
            
        if self.audio.play_song(local_path, start_time=start_offset):
            self.local_is_paused = False 
            self.last_played_id = song.id
            self.state.current_duration = self._get_duration(local_path)
            self._broadcast('NOW_PLAYING', {'song': song})
            self._broadcast('PLAYBACK_SYNC', {'pos': start_offset, 'dur': self.state.current_duration})
            self._broadcast('FULL_STATE_SYNC', {'playlist': self.state.playlist, 'current_song': song})
            self.ui.update_play_pause_icon(True)

    def _refresh_ui(self):
        if not hasattr(self, 'ui'): return
        is_host = self.election.is_host
        leader = self.state.get_host()
        self.ui.set_controls_visible(is_host, host_id=leader)
        cp = self.state.current_song
        
        # PEER SIDE CHECK with Smart Path
        if cp:
            local_path = self._resolve_local_path(cp.file_path)
            if not local_path:
                self.ui.update_now_playing(f"[MISSING] {cp.title}", cp.artist)
            else:
                self.ui.update_now_playing(cp.title, cp.artist)
        else:
            self.ui.update_now_playing("Nothing Playing", "Unknown")
            
        self.ui.update_playlist(self.state.playlist)
        self.ui.update_progress(self.state.current_song_pos, getattr(self.state, 'current_duration', 0))
        self.ui.update_toggles(self.state.repeat_mode, self.is_shuffle_active)

    def _maintenance_loop(self):
        time.sleep(2) 
        debug_timer = time.time() + 3
        while self.running:
            try:
                self.state.update_my_uptime()
                
                if hasattr(self.state, 'lock'):
                    with self.state.lock:
                        if self.node_id in self.state.peers:
                            self.state.peers[self.node_id]['uptime'] = self.state.get_uptime()
                
                self._refresh_ui()
                
                if time.time() > debug_timer:
                    # print(f"\n--- [DEBUG] ...") # Silent debug for production feel
                    debug_timer = time.time() + 3
                
                if self.election.is_host:
                    for pid in list(self.network.connections.keys()):
                        self.network.send_to_peer(pid, 'HEARTBEAT', payload={
                            'uptime': self.state.get_uptime(),
                            'battery': self._get_battery_level()
                        })
                        self.election.update_heartbeat()

                    if self.audio.is_busy() or self.local_is_paused:
                        current_pos = self.audio.get_current_pos()
                        self.state.current_song_pos = current_pos
                        self._broadcast('PLAYBACK_SYNC', {'pos': current_pos, 'dur': getattr(self.state, 'current_duration', 0)})
                    else:
                        target_song = None
                        start_offset = 0
                        if self.state.current_song and self.state.current_song.id != self.last_played_id:
                            target_song = self.state.current_song
                            start_offset = self.state.current_song_pos
                        elif self.state.repeat_mode == 2 and self.state.current_song:
                            target_song = self.state.current_song
                        elif len(self.state.playlist) > 0:
                            if self.state.repeat_mode == 1 and self.state.current_song:
                                self.state.playlist.append(self.state.current_song)
                            target_song = self.state.playlist.pop(0)
                            if self.state.current_song and self.state.current_song.id != target_song.id:
                                self.history.append(self.state.current_song)
                            self.state.current_song = target_song
                            self.state.current_song_pos = 0
                        elif self.state.current_song is not None:
                            self.ui_log("Playlist complete.")
                            self.state.current_song = None
                            self._broadcast('NOW_PLAYING', {'song': None})

                        if target_song:
                            self._play_song_logic(target_song, start_offset)
                    
                else:
                    host = self.state.get_host()
                    if host and host in self.network.connections:
                         self.network.send_to_peer(host, 'HEARTBEAT', payload={
                            'uptime': self.state.get_uptime(),
                            'battery': self._get_battery_level()
                        })

                self.election.check_for_host_failure()
                time.sleep(HEARTBEAT_INTERVAL)
            except Exception as e:
                print(f"Error in maintenance loop: {e}")

    def start(self):
        self.network.start_server()
        self.discovery.start_listener(self.on_peer_discovered)
        self.discovery.broadcast_presence()
        self.discovery.start_periodic_broadcast()  # Re-broadcast periodically for new nodes
        threading.Thread(target=self._maintenance_loop, daemon=True).start()
        self.ui_log(f"Node started. ID: {self.node_id}")
        
        def delayed_election():
            time.sleep(3.0)
            # Only start election if we don't already know about a host
            existing_host = self.state.get_host()
            if existing_host and existing_host != self.node_id:
                self.ui_log(f"Skipping election - host {existing_host} already exists")
                return
            self.ui_log(f"Starting ELECTION (Score-Based)")
            self.election.start_election()
        threading.Thread(target=delayed_election, daemon=True).start()
        
        self.ui.run()

    def on_peer_discovered(self, pid, ip, port):
        if str(pid) != str(self.node_id):
            # Check if we are already connected
            if self.network.connections.get(str(pid)):
                # Connection exists (likely incoming), but we must ensure we've said HELLO
                # so the Host knows to Welcome/Sync us.
                self.network.send_to_peer(pid, 'HELLO', payload={'id': self.node_id})
            else:
                # No connection yet, establish it (sends HELLO automatically)
                self.network.connect_to_peer(pid, ip, port)

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else None
    pwd = sys.argv[2] if len(sys.argv) > 2 else None
    
    node = CollaborativeNode(name, pwd)
    node.start()