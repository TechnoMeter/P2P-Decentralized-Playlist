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

class CollaborativeNode:
    """Main controller for the Decentralized Playlist."""
    
    def __init__(self, node_id=None):
        self.node_id = node_id or str(uuid.uuid4())[:8]
        self.tcp_port = self._find_available_port(TCP_PORT)
        
        self.ui = PlaylistUI(self.node_id, self.on_add_song_request)
        self.state = StateManager(self.node_id, self.ui_log)
        self.state.current_song = None
        self.state.current_song_pos = 0 
        
        self.network = NetworkNode(self.node_id, self.state, self.ui_log)
        self.network.port = self.tcp_port
        
        self.election = ElectionManager(self.node_id, self.network, self.ui_log)
        self.network.election = self.election 
        
        self.discovery = DiscoveryManager(self.node_id, self.tcp_port, self.ui_log)
        self.audio = AudioEngine(self.ui_log)
        self.network.audio = self.audio
        
        self.ui.on_skip = self.on_skip_request
        self.ui.on_volume = self.audio.set_volume
        
        self.last_played_id = None
        self.running = True
        self.force_ui_update = False
        self.discovered_peers = set()
        self.host_initialization_done = False
        self.host_initialization_start = 0
        self.waiting_for_position = False
        self.position_wait_start = 0
        self.initial_playback_done = False

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

    def on_add_song_request(self, file_path):
        title = file_path.replace("\\", "/").split("/")[-1]
        new_song = Song(title=title, added_by=self.node_id, file_path=file_path)
        self.state.add_song(new_song)
        
        # Broadcast to all connected peers
        for pid in list(self.network.connections.keys()):
            self.network.send_to_peer(pid, 'QUEUE_SYNC', payload={'song': new_song})
        
        self.ui_log(f"Added song '{title}' to queue")

    def on_skip_request(self):
        if not self.election.is_host:
            self.ui_log("Skip ignored: Only the Host can control playback.")
            return
        self.ui_log("Skip requested...")
        self.audio.stop()

    def _refresh_ui(self):
        if not hasattr(self, 'ui'): return
        
        is_host = self.election.is_host
        leader = self.election.leader_id
        
        # Update Role and toggle controls visibility
        self.ui.set_controls_visible(is_host)
        
        if is_host:
            if not self.host_initialization_done:
                status = "HOST (Initializing...)"
            else:
                status = "HOST (Leader)"
        elif leader:
            status = f"Follower (Host: {leader})"
        else:
            # If we have connections but no leader, check if we're in election
            if self.election.is_election_running:
                status = "Election in progress..."
            elif len(self.network.connections) > 0:
                status = "Connected, waiting for leader..."
            else:
                status = "Disconnected"
        self.ui.status_label.config(text=f"Role: {status}")
        
        cp = self.state.current_song
        if cp:
            # Get current position for display
            if is_host and self.audio.is_busy():
                current_pos = self.audio.get_current_pos()
            else:
                current_pos = getattr(self.state, 'current_song_pos', 0)
            
            # Format time as MM:SS
            minutes = int(current_pos // 60)
            seconds = int(current_pos % 60)
            display_text = f"{cp.title} ({minutes}:{seconds:02d})"
            self.ui.now_playing_label.config(text=display_text)
            
            # Update state position for consistency
            self.state.current_song_pos = current_pos
        else:
            self.ui.now_playing_label.config(text="Nothing is playing")

        # Update playlist display
        current_children = self.ui.tree.get_children()
        
        # Clear and rebuild if needed
        for item in current_children: 
            self.ui.tree.delete(item)
        
        for song in self.state.playlist:
            self.ui.tree.insert("", "end", values=(song.title, "Unknown", song.added_by))

    def _periodic_discovery(self):
        """Periodically broadcast presence to discover peers."""
        while self.running:
            self.discovery.broadcast_presence()
            time.sleep(5)

    def _maintenance_loop(self):
        time.sleep(2) 
        while self.running:
            self._refresh_ui()
            
            if self.election.is_host:
                # Send heartbeats to all connected peers
                for pid in list(self.network.connections.keys()):
                    self.network.send_to_peer(pid, 'HEARTBEAT')
                
                # Check if we just became host and need initialization
                if self.election.just_became_host and not self.host_initialization_done:
                    if self.host_initialization_start == 0:
                        self.host_initialization_start = time.time()
                        self.ui_log("Starting host initialization...")
                        self.initial_playback_done = False
                        # Request current position from all peers
                        if self.state.current_song:
                            self.ui_log(f"Requesting current position for: {self.state.current_song.title}")
                            for pid in list(self.network.connections.keys()):
                                self.network.send_to_peer(pid, 'CURRENT_POSITION_REQUEST')
                            self.waiting_for_position = True
                            self.position_wait_start = time.time()
                    
                    # Wait for initialization to complete
                    if time.time() - self.host_initialization_start < 3.0:
                        if self.waiting_for_position and time.time() - self.position_wait_start > 1.5:
                            self.waiting_for_position = False
                            self.ui_log("Position wait timeout, proceeding with current position")
                        
                        if self.waiting_for_position:
                            for pid in list(self.network.connections.keys()):
                                self.network.send_to_peer(pid, 'HEARTBEAT')
                            time.sleep(HEARTBEAT_INTERVAL)
                            continue
                    else:
                        self.host_initialization_done = True
                        self.election.just_became_host = False
                        self.waiting_for_position = False
                        self.ui_log("Host initialization complete. Starting playback...")
                
                # Check for higher nodes and step down if found
                higher_nodes = []
                
                for pid in list(self.network.connections.keys()):
                    if pid > self.node_id:
                        higher_nodes.append(pid)
                
                for pid in self.network.state.peers.keys():
                    if pid > self.node_id and pid not in higher_nodes:
                        higher_nodes.append(pid)
                
                if higher_nodes and not self.election.is_election_running:
                    self.ui_log(f"Higher nodes detected: {higher_nodes}. Stepping down as host.")
                    self.election.is_host = False
                    self.election.leader_id = None
                    self.election.start_election()
                    self.host_initialization_done = False
                    self.host_initialization_start = 0
                    self.waiting_for_position = False
                    self.initial_playback_done = False
                    if self.audio.is_busy():
                        self.audio.stop()
                    self.state.current_song = None
                    self.state.current_song_pos = 0
                    self.last_played_id = None
                    self.force_ui_update = True
                    continue

                # Continue with normal host duties...
                if self.audio.is_busy():
                    current_pos = self.audio.get_current_pos()
                    self.state.current_song_pos = current_pos
                    # Sync position with followers
                    for pid in list(self.network.connections.keys()):
                        self.network.send_to_peer(pid, 'PLAYBACK_SYNC', payload={'pos': current_pos})
                elif self.host_initialization_done and not self.initial_playback_done:
                    # Initial playback after becoming host
                    target_song = None
                    start_offset = 0
                    
                    if self.state.current_song:
                        target_song = self.state.current_song
                        start_offset = getattr(self.state, 'current_song_pos', 0)
                        self.ui_log(f"Resuming {target_song.title} from {start_offset:.1f}s")
                        for pid in list(self.network.connections.keys()):
                            self.network.send_to_peer(pid, 'NOW_PLAYING', payload={'song': target_song})
                    else:
                        if len(self.state.playlist) > 0:
                            target_song = self.state.playlist.pop(0)
                            self.state.current_song = target_song
                            self.state.current_song_pos = 0
                            start_offset = 0
                            for pid in list(self.network.connections.keys()):
                                self.network.send_to_peer(pid, 'NOW_PLAYING', payload={'song': target_song})
                                self.network.send_to_peer(pid, 'REMOVE_SONG', payload={'song_id': target_song.id})

                    if target_song:
                        if self.audio.play_song(target_song.file_path, start_time=start_offset):
                            self.last_played_id = target_song.id
                            self.initial_playback_done = True
                            self.ui_log(f"Now playing: {target_song.title}")
                            # Immediately update UI with correct position
                            self._refresh_ui()
                        else:
                            self.ui_log(f"Failed to play: {target_song.title}. Skipping.")
                            self.last_played_id = target_song.id
                            self.state.current_song = None
                            self.state.current_song_pos = 0
                            self.initial_playback_done = True
                elif self.host_initialization_done and self.initial_playback_done:
                    # Normal playback after initial playback
                    if not self.audio.is_busy():
                        if self.state.current_song:
                            self.ui_log(f"Current song finished, moving to next")
                            self.state.current_song = None
                            self.state.current_song_pos = 0
                            self.last_played_id = None
                        
                        if len(self.state.playlist) > 0:
                            target_song = self.state.playlist.pop(0)
                            self.state.current_song = target_song
                            self.state.current_song_pos = 0
                            
                            if self.audio.play_song(target_song.file_path):
                                self.last_played_id = target_song.id
                                self.ui_log(f"Now playing: {target_song.title}")
                                for pid in list(self.network.connections.keys()):
                                    self.network.send_to_peer(pid, 'NOW_PLAYING', payload={'song': target_song})
                                    self.network.send_to_peer(pid, 'REMOVE_SONG', payload={'song_id': target_song.id})
                            else:
                                self.ui_log(f"Failed to play: {target_song.title}. Skipping.")
                                self.state.current_song = None
                                self.state.current_song_pos = 0
                        elif self.state.current_song is not None:
                            self.ui_log("Playlist complete.")
                            self.state.current_song = None
                            self.state.current_song_pos = 0
                            for pid in list(self.network.connections.keys()):
                                self.network.send_to_peer(pid, 'NOW_PLAYING', payload={'song': None})

            self.election.check_for_host_failure()
            time.sleep(HEARTBEAT_INTERVAL)

    def start(self):
        self.network.start_server()
        self.discovery.start_listener(self.on_peer_discovered)
        self.discovery.broadcast_presence()
        
        threading.Thread(target=self._periodic_discovery, daemon=True).start()
        threading.Thread(target=self._maintenance_loop, daemon=True).start()
        
        def delayed_election():
            time.sleep(2.0)
            if not self.election.is_host:
                self.election.start_election()
        threading.Thread(target=delayed_election, daemon=True).start()
        
        self.ui.run()

    def on_peer_discovered(self, pid, ip, port):
        if str(pid) != str(self.node_id) and pid not in self.discovered_peers:
            self.discovered_peers.add(pid)
            self.ui_log(f"Discovered peer {pid} at {ip}:{port}. Connecting...")
            
            self.network.connect_to_peer(pid, ip, port)
            
            if self.election.is_host and str(pid) > str(self.node_id):
                self.ui_log(f"Higher-ID peer {pid} joined. Stepping down as host.")
                self.election.is_host = False
                self.election.leader_id = None
                if self.audio.is_busy():
                    self.audio.stop()
                self.host_initialization_done = False
                self.host_initialization_start = 0
                self.waiting_for_position = False
                self.initial_playback_done = False
                self.force_ui_update = True
                threading.Timer(1.0, self.election.start_election).start()

if __name__ == "__main__":
    cid = sys.argv[1] if len(sys.argv) > 1 else None
    node = CollaborativeNode(cid)
    node.start()