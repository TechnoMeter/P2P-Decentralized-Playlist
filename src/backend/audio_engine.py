import pygame
import os

class AudioEngine:
    """Handles local audio playback using pygame. Supports seeking and volume control."""
    
    def __init__(self, logger_callback=None):
        self.logger = logger_callback
        self.is_playing = False
        self.start_offset = 0  # Track the offset we started playing from
        self.playback_start_time = 0  # Track when playback started
        
        try:
            pygame.mixer.init()
            # Set default volume
            pygame.mixer.music.set_volume(0.7)
            self.log("Audio Engine initialized.")
        except Exception as e:
            self.log(f"Failed to initialize audio: {e}")

    def log(self, text):
        if self.logger: self.logger(f"[Audio] {text}")

    def play_song(self, song_path, start_time=0):
        """Plays a song, optionally starting from a specific offset in seconds."""
        if not song_path or not os.path.exists(song_path):
            self.log(f"Playback error: File not found at {song_path}")
            return False

        try:
            pygame.mixer.music.load(song_path)
            # Store the start offset
            self.start_offset = start_time
            self.playback_start_time = pygame.time.get_ticks()
            
            # Play from the specified start time
            pygame.mixer.music.play(start=start_time)
            self.is_playing = True
            
            if start_time > 0:
                self.log(f"Resuming: {os.path.basename(song_path)} at {start_time:.1f}s")
            else:
                self.log(f"Playing: {os.path.basename(song_path)}")
            return True
        except Exception as e:
            self.log(f"Pygame error: {e}")
            return False

    def get_current_pos(self):
        """Returns the current absolute playback position in seconds."""
        if self.is_busy():
            # pygame.mixer.music.get_pos() returns milliseconds since play() was called
            # We need to add the start offset to get absolute position
            elapsed_ms = pygame.mixer.music.get_pos()
            elapsed_seconds = elapsed_ms / 1000.0
            return self.start_offset + elapsed_seconds
        return 0

    def set_volume(self, volume):
        """Sets the music volume (0.0 to 1.0)."""
        try:
            pygame.mixer.music.set_volume(volume)
        except:
            pass

    def is_busy(self):
        try:
            return pygame.mixer.music.get_busy()
        except:
            return False

    def stop(self):
        pygame.mixer.music.stop()
        self.is_playing = False
        self.start_offset = 0