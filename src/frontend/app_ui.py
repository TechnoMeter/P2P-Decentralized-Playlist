import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import queue
from src.frontend.styles import *

class PlaylistUI:
    """
    Frutiger Aero / Vista inspired UI implementation.
    Features a pinned bottom player skin (Deep Blue), checkbox-based selection,
    and aero-styled management buttons.
    """
    
    def __init__(self, node_id, on_add_song_callback):
        self.node_id = node_id
        self.root = tk.Tk()
        self.root.title(f"P2P Playlist - {node_id}")
        self.root.geometry("850x650")
        self.root.configure(bg=BG_MAIN)
        
        # Callbacks
        self.on_add_song = on_add_song_callback
        self.on_skip_next = None
        self.on_skip_prev = None
        self.on_play_pause = None
        self.on_seek = None
        self.on_shuffle = None
        self.on_repeat = None
        self.on_clear_queue = None
        self.on_remove_song = None
        self.on_volume_change = None
        
        self.msg_queue = queue.Queue()
        self.controls_visible = False 
        self.debug_visible = False
        self.is_dragging_seek = False # Track if user is currently sliding
        
        self._setup_styles()
        self._setup_layout()
        self._start_queue_listener()

    def _setup_styles(self):
        """Configures ttk styles for a glossy/aero look."""
        style = ttk.Style()
        style.theme_use('clam') 
        
        # Treeview (Playlist)
        style.configure("Treeview", 
                        background=BG_MAIN, 
                        foreground=TEXT_MAIN, 
                        fieldbackground=BG_MAIN,
                        borderwidth=0,
                        font=FONT_NORMAL,
                        rowheight=28)
        
        style.configure("Treeview.Heading", 
                        background=BG_PANEL, 
                        foreground=ACCENT, 
                        font=("Segoe UI", 9, "bold"),
                        relief="raised") 
        
        style.map("Treeview", 
                  background=[('selected', '#333333')], 
                  foreground=[('selected', 'white')])
        
        # Slider Styles with Distinct States
        style.configure("Horizontal.TScale", 
                        background=BG_PLAYER, 
                        troughcolor="#004488",  # Vibrant Blue for Active
                        sliderlength=20,
                        sliderthickness=15,
                        borderwidth=1,
                        relief="raised")
        
        # Map specific colors for the disabled state to ensure high contrast/greyout
        style.map("Horizontal.TScale",
                  troughcolor=[('disabled', '#2b2b2b')],  # Dark Grey/Black for Disabled
                  sliderrelief=[('disabled', 'flat')],    # Flat look when disabled
                  background=[('disabled', BG_PLAYER)])   # Keep background blended

    def _setup_layout(self):
        # --- 1. Header (Top Bar) ---
        self.header = tk.Frame(self.root, bg=BG_HEADER, height=50, bd=1, relief="raised")
        self.header.pack(side="top", fill="x")
        
        # App Title
        tk.Label(self.header, text="P2P PLAYLIST", bg=BG_HEADER, fg=ACCENT, font=("Segoe UI", 14, "bold", "italic")).pack(side="left", padx=PAD_L)
        
        # Status Badge
        self.status_label = tk.Label(self.header, text="Connecting...", bg=BG_HEADER, fg=TEXT_SUB, font=FONT_SMALL)
        self.status_label.pack(side="left", padx=PAD_M)
        
        # Header Tools 
        self.debug_btn = tk.Button(self.header, text="CMD 💻", bg=BG_HEADER, fg=TEXT_SUB,
                                 font=("Segoe UI", 9), relief="flat", activebackground=BG_PLAYER, 
                                 command=self.toggle_debug)
        self.debug_btn.pack(side="right", padx=PAD_M, pady=PAD_M)
        
        add_btn = tk.Button(self.header, text="+ ADD TRACK", bg=ACCENT, fg="#000000",
                           font=("Segoe UI", 9, "bold"), relief="raised", bd=2, padx=15,
                           activebackground=ACCENT_HOVER,
                           command=self._add_song_dialog)
        add_btn.pack(side="right", padx=PAD_M, pady=PAD_M)

        # --- 2. Pinned Player Skin (Bottom) ---
        self.player_frame = tk.Frame(self.root, bg=BG_PLAYER, bd=0)
        self.player_frame.pack(side="bottom", fill="x")
        
        tk.Frame(self.player_frame, bg=ACCENT, height=2).pack(side="top", fill="x")
        
        self._setup_player_ui()

        # --- 3. Main Content Container ---
        self.middle_container = tk.Frame(self.root, bg=BG_MAIN)
        self.middle_container.pack(side="top", fill="both", expand=True)

        # Playlist Panel
        self.playlist_panel = tk.Frame(self.middle_container, bg=BG_MAIN)
        self.playlist_panel.pack(side="left", fill="both", expand=True, padx=PAD_M, pady=PAD_M)
        
        list_header = tk.Frame(self.playlist_panel, bg=BG_MAIN)
        list_header.pack(fill="x", pady=(0, 5))
        
        tk.Label(list_header, text="Current Queue", bg=BG_MAIN, fg=TEXT_MAIN, font=FONT_TITLE).pack(side="left")

        # Treeview with Checkbox Column
        columns = ("select", "title", "artist", "added_by")
        self.tree = ttk.Treeview(self.playlist_panel, columns=columns, show="headings", selectmode="browse")
        
        self.tree.heading("select", text="✔")
        self.tree.column("select", width=40, anchor="center")
        self.tree.heading("title", text="Title")
        self.tree.heading("artist", text="Artist")
        self.tree.heading("added_by", text="Added By")
        self.tree.column("title", width=300)
        self.tree.column("artist", width=150)
        self.tree.column("added_by", width=100)
        
        self.tree.pack(side="top", fill="both", expand=True)
        self.tree.bind("<Button-1>", self._on_tree_click)
        
        scrollbar = ttk.Scrollbar(self.tree, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        # --- 4. Aero Toolbar (Bottom of Playlist) ---
        self.toolbar_frame = tk.Frame(self.playlist_panel, bg=BG_MAIN, height=50)
        self.toolbar_frame.pack(fill="x", pady=10)
        
        self.btn_style = {"font": ("Segoe UI", 9, "bold"), "relief": "raised", "bd": 2, "padx": 15, "pady": 5, "activebackground": ACCENT_HOVER}
        
        self.remove_btn = tk.Button(self.toolbar_frame, text="Remove Selected", bg=BTN_DISABLED_BG, fg=TEXT_DISABLED,
                                  state="disabled", command=self._handle_remove_checked, **self.btn_style)
        self.remove_btn.pack(side="left", padx=(0, 10))

        self.btn_clear_list = tk.Button(self.toolbar_frame, text="Clear Playlist", bg=ACCENT, fg="#000000",
                                      state="disabled", command=lambda: self._trigger(self.on_clear_queue), **self.btn_style)
        self.btn_clear_list.pack(side="left")

        # --- Debug Panel ---
        self.debug_panel = tk.Frame(self.middle_container, bg=BG_TERM, width=300, bd=2, relief="sunken")
        
        # Terminal Header (Title + Copy Tools)
        term_header = tk.Frame(self.debug_panel, bg=BG_TERM)
        term_header.pack(fill="x", pady=2, padx=2)
        
        tk.Label(term_header, text="SYSTEM TERMINAL", bg=BG_TERM, fg=TEXT_TERM, font=("Consolas", 10, "bold")).pack(side="left")
        
        # Copy Buttons
        copy_opts = {"bg": BG_PLAYER, "fg": ACCENT, "relief": "flat", "font": ("Segoe UI", 8), "padx": 5, "activebackground": BG_PANEL, "activeforeground": ACCENT}
        tk.Button(term_header, text="Copy All", command=self._copy_all_logs, **copy_opts).pack(side="right", padx=2)
        tk.Button(term_header, text="Copy Sel", command=self._copy_selection_logs, **copy_opts).pack(side="right", padx=2)

        # Log Box with Scrollbar
        log_frame = tk.Frame(self.debug_panel, bg=BG_TERM)
        log_frame.pack(fill="both", expand=True, padx=2, pady=2)

        term_scrollbar = ttk.Scrollbar(log_frame, orient="vertical")
        self.log_box = tk.Text(log_frame, bg=BG_TERM, fg=TEXT_TERM, font=FONT_MONO, borderwidth=0, state="disabled", 
                               yscrollcommand=term_scrollbar.set, selectbackground="#333333", selectforeground="white")
        term_scrollbar.config(command=self.log_box.yview)
        
        term_scrollbar.pack(side="right", fill="y")
        self.log_box.pack(side="left", fill="both", expand=True)

    def _setup_player_ui(self):
        """Constructs the pinned player bar with a Deep Blue 'skin' aesthetic."""
        
        # A. Seek Bar Row
        self.seek_frame = tk.Frame(self.player_frame, bg=BG_PLAYER, pady=5)
        self.seek_frame.pack(fill="x", padx=PAD_L)
        
        self.lbl_current_time = tk.Label(self.seek_frame, text="0:00", bg=BG_PLAYER, fg=ACCENT, font=FONT_SMALL)
        self.lbl_current_time.pack(side="left")
        
        self.seek_slider = ttk.Scale(self.seek_frame, from_=0, to=100, orient="horizontal")
        # Bind events for flawless seeking
        self.seek_slider.bind("<ButtonPress-1>", self._on_seek_start)
        self.seek_slider.bind("<ButtonRelease-1>", self._on_seek_end)
        self.seek_slider.pack(side="left", fill="x", expand=True, padx=PAD_M)
        
        self.lbl_total_time = tk.Label(self.seek_frame, text="0:00", bg=BG_PLAYER, fg=TEXT_SUB, font=FONT_SMALL)
        self.lbl_total_time.pack(side="right")

        # B. Controls Row
        controls_frame = tk.Frame(self.player_frame, bg=BG_PLAYER, pady=10)
        controls_frame.pack(fill="x", padx=PAD_L, pady=(0, 10))

        # B1. Song Info
        info_sub = tk.Frame(controls_frame, bg=BG_PLAYER, width=220)
        info_sub.pack(side="left", fill="y")
        info_sub.pack_propagate(False)
        self.now_playing_title = tk.Label(info_sub, text="Waiting for Host...", bg=BG_PLAYER, fg=ACCENT, font=("Segoe UI", 11, "bold"), anchor="w")
        self.now_playing_title.pack(fill="x")
        self.now_playing_artist = tk.Label(info_sub, text="--", bg=BG_PLAYER, fg=TEXT_MAIN, font=FONT_SMALL, anchor="w")
        self.now_playing_artist.pack(fill="x")

        # B2. Playback Controls
        self.host_controls = tk.Frame(controls_frame, bg=BG_PLAYER)
        self.host_controls.pack(side="left", expand=True)
        
        # Control Buttons
        btn_opts = {'bg': BG_PLAYER, 'fg': ACCENT, 'relief': 'flat', 'activebackground': BG_PLAYER, 'bd': 0, 'font': ("Segoe UI Symbol", 14)}
        
        self.btn_shuffle = tk.Button(self.host_controls, text="🔀", **btn_opts, command=lambda: self._trigger(self.on_shuffle))
        self.btn_shuffle.pack(side="left", padx=8)
        
        self.btn_prev = tk.Button(self.host_controls, text="⏮", **btn_opts, command=lambda: self._trigger(self.on_skip_prev))
        self.btn_prev.pack(side="left", padx=8)
        
        self.btn_play = tk.Button(self.host_controls, text="▶", bg=ACCENT, fg="#000000", font=("Segoe UI Symbol", 16), 
                                relief="raised", bd=3, width=4, activebackground=ACCENT_HOVER,
                                command=lambda: self._trigger(self.on_play_pause))
        self.btn_play.pack(side="left", padx=15)
        
        self.btn_next = tk.Button(self.host_controls, text="⏭", **btn_opts, command=lambda: self._trigger(self.on_skip_next))
        self.btn_next.pack(side="left", padx=8)
        
        self.btn_repeat = tk.Button(self.host_controls, text="🔁", **btn_opts, command=lambda: self._trigger(self.on_repeat))
        self.btn_repeat.pack(side="left", padx=8)

        # B3. Volume
        tools_sub = tk.Frame(controls_frame, bg=BG_PLAYER)
        tools_sub.pack(side="right")
        tk.Label(tools_sub, text="🔊", bg=BG_PLAYER, fg=ACCENT).pack(side="left", padx=2)
        self.vol_slider = ttk.Scale(tools_sub, from_=0, to=100, orient="horizontal", command=self._handle_volume)
        self.vol_slider.set(70)
        self.vol_slider.pack(side="left", padx=5)

    # --- Interaction Logic ---
    
    def _on_seek_start(self, event):
        self.is_dragging_seek = True
        
    def _on_seek_end(self, event):
        self.is_dragging_seek = False
        if self.on_seek and self.controls_visible:
            # Get value from slider
            val = self.seek_slider.get()
            self.on_seek(float(val))

    def _on_tree_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region == "cell":
            col = self.tree.identify_column(event.x)
            if col == "#1":
                item_id = self.tree.identify_row(event.y)
                if item_id:
                    current_values = self.tree.item(item_id, "values")
                    current_status = current_values[0]
                    new_status = "☑" if current_status == "☐" else "☐"
                    new_values = (new_status,) + current_values[1:]
                    self.tree.item(item_id, values=new_values)
                    self._check_selection_state()

    def _check_selection_state(self):
        if not self.controls_visible: return
        has_checked = False
        for child in self.tree.get_children():
            if self.tree.item(child, "values")[0] == "☑":
                has_checked = True
                break
        
        if has_checked:
            self.remove_btn.config(state="normal", bg=ACCENT, fg="#000000")
        else:
            self.remove_btn.config(state="disabled", bg=BTN_DISABLED_BG, fg=TEXT_DISABLED)

    def _handle_remove_checked(self):
        if not self.on_remove_song: return
        items_to_remove = []
        for child in self.tree.get_children():
            val = self.tree.item(child, "values")
            if val[0] == "☑":
                song_data = (val[1], val[2], val[3]) 
                items_to_remove.append(song_data)
        for song_data in items_to_remove:
            self.on_remove_song(song_data)

    def set_controls_visible(self, is_host, host_id=None):
        self.controls_visible = is_host
        state = "normal" if is_host else "disabled"
        
        # 1. Host controls (Play, Pause, Skip, etc.)
        for widget in self.host_controls.winfo_children():
            widget.configure(state=state)
        
        # 2. Seek Bar (Ensure it is VISIBLE but DISABLED if not host)
        if not self.seek_slider.winfo_ismapped():
            self.seek_slider.pack(side="left", fill="x", expand=True, padx=PAD_M)
        self.seek_slider.configure(state=state)

        # 3. Volume Slider
        self.vol_slider.configure(state=state)

        # 4. Clear/Remove Buttons
        self.btn_clear_list.configure(state=state)
        if not is_host:
            self.remove_btn.config(state="disabled", bg=BTN_DISABLED_BG, fg=TEXT_DISABLED)
        else:
            self._check_selection_state()
        
        # Role text update
        if is_host:
            role_text = f"HOST (You: {self.node_id})"
            fg_color = TEXT_HOST
        elif host_id:
            role_text = f"LISTENER (Host: {host_id})"
            fg_color = TEXT_HOST
        else:
            role_text = "Finding Host..."
            fg_color = ACCENT_WARNING
        font = ("Segoe UI", 9, "bold") if (is_host or host_id) else FONT_SMALL
        self.status_label.config(text=role_text, fg=fg_color, font=font)

    # --- UI Update Methods ---
    
    def update_play_pause_icon(self, is_playing):
        """Swaps the play button icon."""
        icon = "⏸" if is_playing else "▶"
        self.btn_play.config(text=icon)

    def update_progress(self, current_seconds, total_seconds):
        """Updates the slider and time labels correctly."""
        # Format M:SS
        def fmt_time(s):
            m, s = divmod(int(s), 60)
            return f"{m}:{s:02d}"

        self.lbl_current_time.config(text=fmt_time(current_seconds))
        self.lbl_total_time.config(text=fmt_time(total_seconds))
        
        # Only update slider if user is NOT dragging it
        if not self.is_dragging_seek and total_seconds > 0:
            pct = (current_seconds / total_seconds) * 100
            self.seek_slider.set(pct)

    def update_toggles(self, repeat_mode, is_shuffle):
        """Updates the visual state of toggle buttons (pressed look)."""
        # Repeat: 0=Off, 1=All, 2=One
        if repeat_mode == 0:
            self.btn_repeat.config(bg=BG_PLAYER, relief="flat", text="🔁")
        elif repeat_mode == 1:
            self.btn_repeat.config(bg=BTN_ACTIVE_BG, relief="sunken", text="🔁")
        else:
            self.btn_repeat.config(bg=BTN_ACTIVE_BG, relief="sunken", text="🔂") # One

        # Shuffle
        if is_shuffle:
            self.btn_shuffle.config(bg=BTN_ACTIVE_BG, relief="sunken")
        else:
            self.btn_shuffle.config(bg=BG_PLAYER, relief="flat")

    def update_now_playing(self, title, artist="Unknown"):
        self.now_playing_title.config(text=title if title else "Nothing Playing")
        self.now_playing_artist.config(text=artist)

    def update_playlist(self, songs):
        current_items = self.tree.get_children()
        current_data = []
        for item_id in current_items:
            vals = self.tree.item(item_id, "values")
            if vals:
                current_data.append((vals[1], vals[2], vals[3]))
        new_data = [(song.title, song.artist, song.added_by) for song in songs]
        if current_data == new_data:
            return 
        checked_titles = set()
        for item_id in current_items:
            vals = self.tree.item(item_id, "values")
            if vals and vals[0] == "☑":
                checked_titles.add(vals[1])
        for i in self.tree.get_children():
            self.tree.delete(i)
        for song in songs:
            check_mark = "☑" if song.title in checked_titles else "☐"
            self.tree.insert("", "end", values=(check_mark, song.title, song.artist, song.added_by))
        self._check_selection_state()

    def _trigger(self, callback):
        if callback and self.controls_visible:
            callback()

    def _handle_volume(self, value):
        if self.on_volume_change:
            self.on_volume_change(float(value) / 100.0)

    def _add_song_dialog(self):
        file_path = filedialog.askopenfilename(filetypes=[("Audio Files", "*.mp3 *.wav *.ogg")])
        if file_path:
            self.on_add_song(file_path)

    def log_message(self, message):
        self.msg_queue.put(message)

    # --- Terminal Helpers ---
    def _copy_all_logs(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log_box.get("1.0", "end-1c"))
        
    def _copy_selection_logs(self):
        try:
            sel = self.log_box.get("sel.first", "sel.last")
            if sel:
                self.root.clipboard_clear()
                self.root.clipboard_append(sel)
        except tk.TclError:
            pass # No selection available

    def toggle_debug(self):
        if self.debug_visible:
            self.debug_panel.pack_forget()
            self.root.geometry("850x650")
            self.debug_btn.config(relief="flat", bg=BG_PLAYER)
        else:
            self.debug_panel.pack(side="right", fill="both", padx=(0, 0), pady=0)
            self.root.geometry("1150x650") 
            self.debug_btn.config(relief="sunken", bg=BTN_ACTIVE_BG)
            # Scroll to end when opening
            self.log_box.see("end")
        self.debug_visible = not self.debug_visible

    def _start_queue_listener(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                
                # Smart Scrolling: Check if we are at the bottom BEFORE inserting
                # yview returns (top, bottom). If bottom is 1.0, we are at the end.
                should_scroll = self.log_box.yview()[1] == 1.0
                
                self.log_box.config(state="normal")
                self.log_box.insert("end", f"{msg}\n")
                
                if should_scroll:
                    self.log_box.see("end")
                    
                self.log_box.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._start_queue_listener)

    def run(self):
        self.root.mainloop()