import tkinter as tk
from tkinter import ttk, filedialog
import queue
from src.frontend.styles import *

class PlaylistUI:
    """The lucrative Dark-Mode interface for the Collaborative Playlist."""
    
    def __init__(self, node_id, on_add_song_callback):
        self.root = tk.Tk()
        self.root.title(f"P2P Playlist - {node_id}")
        self.root.geometry("650x700") 
        self.root.configure(bg=BG_DARK)
        
        self.on_add_song = on_add_song_callback
        self.on_skip = None   
        self.on_volume = None 
        
        self.msg_queue = queue.Queue()
        self.debug_visible = False
        self.controls_visible = True
        
        self._setup_layout()
        self._start_queue_listener()

    def _setup_layout(self):
        # Header
        header = tk.Frame(self.root, bg=BG_CARD, height=60)
        header.pack(fill="x", side="top")
        
        tk.Label(header, text="P2P PLAYLIST", bg=BG_CARD, fg=ACCENT, font=FONT_BOLD).pack(side="left", padx=20)
        
        self.status_label = tk.Label(header, text="Role: Connecting...", bg=BG_CARD, fg=TEXT_DIM, font=FONT_REG)
        self.status_label.pack(side="left", padx=10)

        self.debug_btn = tk.Button(header, text="DEBUG", bg=BG_DARK, fg=TEXT_DIM, 
                                 font=("Segoe UI", 8), command=self.toggle_debug, 
                                 borderwidth=1, relief="flat", padx=10)
        self.debug_btn.pack(side="right", padx=20)
        
        # Main Container
        self.main_container = tk.Frame(self.root, bg=BG_DARK)
        self.main_container.pack(fill="both", expand=True, padx=15, pady=15)
        
        # MUSIC INTERFACE (Left Panel)
        self.left_panel = tk.Frame(self.main_container, bg=BG_DARK)
        self.left_panel.pack(side="left", fill="both", expand=True)
        
        # 1. NOW PLAYING CARD
        self.np_frame = tk.Frame(self.left_panel, bg=BG_CARD, highlightbackground=ACCENT, highlightthickness=1)
        self.np_frame.pack(fill="x", pady=(0, 15))
        
        tk.Label(self.np_frame, text="NOW PLAYING", bg=BG_CARD, fg=ACCENT, font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=15, pady=(12, 0))
        self.now_playing_label = tk.Label(self.np_frame, text="Nothing is playing", bg=BG_CARD, fg=TEXT_MAIN, font=("Segoe UI", 16, "bold"), wraplength=400)
        self.now_playing_label.pack(anchor="w", padx=15, pady=(0, 15))
        
        # Player Controls (Skip & Volume) - Stored as instance to toggle visibility
        self.ctrl_frame = tk.Frame(self.np_frame, bg=BG_CARD)
        self.ctrl_frame.pack(fill="x", padx=15, pady=(0, 15))
        
        tk.Button(self.ctrl_frame, text="⏭ SKIP", bg="#333333", fg="white", font=("Segoe UI", 9, "bold"),
                  command=self._handle_skip, borderwidth=0, padx=15).pack(side="left")
        
        tk.Label(self.ctrl_frame, text="VOL", bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", 8)).pack(side="left", padx=(20, 5))
        self.vol_slider = ttk.Scale(self.ctrl_frame, from_=0, to=100, orient="horizontal", command=self._handle_volume)
        self.vol_slider.set(70) 
        self.vol_slider.pack(side="left", fill="x", expand=True, padx=5)

        # 2. UP NEXT LIST
        tk.Label(self.left_panel, text="Up Next", bg=BG_DARK, fg=TEXT_MAIN, font=FONT_BOLD).pack(anchor="w", pady=(5, 10))
        
        self.tree = ttk.Treeview(self.left_panel, columns=("Title", "Artist", "Added By"), show="headings")
        self.tree.heading("Title", text="Title")
        self.tree.heading("Artist", text="Artist")
        self.tree.heading("Added By", text="Added By")
        self.tree.pack(fill="both", expand=True)
        
        btn_frame = tk.Frame(self.left_panel, bg=BG_DARK)
        btn_frame.pack(fill="x", pady=20)
        tk.Button(btn_frame, text="+ ADD SONG", bg=ACCENT, fg="white", 
                  font=FONT_BOLD, command=self._add_song_dialog, borderwidth=0, padx=30, pady=10).pack(side="right")

        # DEBUG PANEL (Right Panel - Hidden)
        self.debug_panel = tk.Frame(self.main_container, bg=BG_CARD, width=300)
        tk.Label(self.debug_panel, text="Network Activity", bg=BG_CARD, fg=TEXT_MAIN, font=FONT_BOLD).pack(pady=10)
        self.log_box = tk.Text(self.debug_panel, bg=BG_DARK, fg=TEXT_DIM, font=FONT_CONSOLE, state="disabled", borderwidth=0)
        self.log_box.pack(fill="both", expand=True, padx=10, pady=10)

    def set_controls_visible(self, visible):
        """Hides or shows the playback controls (Skip/Volume)."""
        if visible and not self.controls_visible:
            self.ctrl_frame.pack(fill="x", padx=15, pady=(0, 15))
            self.controls_visible = True
        elif not visible and self.controls_visible:
            self.ctrl_frame.pack_forget()
            self.controls_visible = False

    def _handle_skip(self):
        if self.on_skip: self.on_skip()

    def _handle_volume(self, val):
        if self.on_volume:
            self.on_volume(float(val) / 100.0)

    def toggle_debug(self):
        if self.debug_visible:
            self.debug_panel.pack_forget()
            self.root.geometry("650x700")
            self.debug_btn.config(bg=BG_DARK, fg=TEXT_DIM)
        else:
            self.debug_panel.pack(side="right", fill="both", padx=(15, 0))
            self.root.geometry("1000x700") 
            self.debug_btn.config(bg=ACCENT, fg="white")
        self.debug_visible = not self.debug_visible

    def _add_song_dialog(self):
        file_path = filedialog.askopenfilename(filetypes=[("Audio Files", "*.mp3 *.wav")])
        if file_path:
            self.on_add_song(file_path)

    def log_message(self, message):
        self.msg_queue.put(message)

    def _start_queue_listener(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                self.log_box.config(state="normal")
                self.log_box.insert("end", f"{msg}\n")
                self.log_box.see("end")
                self.log_box.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._start_queue_listener)

    def run(self):
        self.root.mainloop()