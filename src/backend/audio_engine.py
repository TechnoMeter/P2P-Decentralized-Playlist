import pygame
import os

class AudioEngine:
    """Handles local audio playback using pygame. Supports seeking and volume control."""
    
    def __init__(self, logger_callback=None):
        self.logger = logger_callback
        self.is_playing = False
        
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
            # pygame.mixer.music.play(loops, start_time_in_seconds)
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
        """Returns the current playback position in seconds."""
        if self.is_busy():
            # get_pos() returns milliseconds since play() was called
            return pygame.mixer.music.get_pos() / 1000.0
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