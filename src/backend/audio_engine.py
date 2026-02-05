import pygame
import os

class AudioEngine:
    """Handles local audio playback using pygame. Supports seeking, pausing, and volume."""
    
    def __init__(self, logger_callback=None):
        self.logger = logger_callback
        self.is_playing = False
        self.is_paused = False
        self.current_file = None
        self.start_offset = 0.0  # Tracks seek position
        
        try:
            pygame.mixer.init()
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
            self.current_file = song_path
            self.start_offset = start_time
            pygame.mixer.music.load(song_path)
            pygame.mixer.music.play(start=start_time)
            self.is_playing = True
            self.is_paused = False
            self.log(f"Playing: {os.path.basename(song_path)} from {start_time}s")
            return True
        except Exception as e:
            self.log(f"Pygame error: {e}")
            return False

    def toggle_pause(self):
        """Toggles between pause and unpause."""
        if not self.is_playing: return False
        
        if self.is_paused:
            pygame.mixer.music.unpause()
            self.is_paused = False
            self.log("Resumed playback.")
            return True # Playing
        else:
            pygame.mixer.music.pause()
            self.is_paused = True
            self.log("Paused playback.")
            return False # Paused

    def seek(self, time_in_seconds):
        """Seeks to a specific position in the current track."""
        if not self.current_file: return
        try:
            # Pygame seeking actually requires reloading/rewinding or using play(start=...)
            # The most reliable way for variable formats is play(start=...)
            pygame.mixer.music.play(start=time_in_seconds)
            self.start_offset = time_in_seconds
            self.is_paused = False # Seeking usually auto-plays
            self.log(f"Seeked to {time_in_seconds}s")
        except Exception as e:
            self.log(f"Seek error: {e}")

    def get_current_pos(self):
        """Returns the current playback position in seconds, accounting for seeks."""
        if self.is_busy() or (self.is_paused and self.is_playing):
            # pygame.mixer.music.get_pos() returns ms since last play() call
            # We must add self.start_offset because play(start=...) resets get_pos() to 0
            return self.start_offset + (pygame.mixer.music.get_pos() / 1000.0)
        return self.start_offset

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
        self.is_paused = False
        self.start_offset = 0