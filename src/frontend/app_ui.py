import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import queue
from src.frontend.styles import *

class PlaylistUI:
    """
    UI implementation.
    Features a pinned bottom player skin (Deep Blue), checkbox-based selection,
    and management buttons.
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
        self.controls_visible = False # Starts false, enables if Host
        self.debug_visible = False
        
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
        
        # Slider 
        style.configure("Horizontal.TScale", 
                        background=BG_PLAYER, 
                        troughcolor="#002244", 
                        sliderlength=20,
                        sliderthickness=15)

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
                                 font=("Segoe UI", 9), relief="flat", activebackground=BG_PLAYER, # Slight shade diff for hover
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
        
        # Glossy Separator Line
        tk.Frame(self.player_frame, bg=ACCENT, height=2).pack(side="top", fill="x")
        
        self._setup_player_ui()

        # --- 3. Main Content Container ---
        self.middle_container = tk.Frame(self.root, bg=BG_MAIN)
        self.middle_container.pack(side="top", fill="both", expand=True)

        # Playlist Panel (Left Side)
        self.playlist_panel = tk.Frame(self.middle_container, bg=BG_MAIN)
        self.playlist_panel.pack(side="left", fill="both", expand=True, padx=PAD_M, pady=PAD_M)
        
        # List Header
        list_header = tk.Frame(self.playlist_panel, bg=BG_MAIN)
        list_header.pack(fill="x", pady=(0, 5))
        
        tk.Label(list_header, text="Current Queue", bg=BG_MAIN, fg=TEXT_MAIN, font=FONT_TITLE).pack(side="left")

        # Treeview with Checkbox Column
        columns = ("select", "title", "artist", "added_by")
        self.tree = ttk.Treeview(self.playlist_panel, columns=columns, show="headings", selectmode="browse")
        
        # Checkbox Column
        self.tree.heading("select", text="✔")
        self.tree.column("select", width=40, anchor="center")
        
        self.tree.heading("title", text="Title")
        self.tree.heading("artist", text="Artist")
        self.tree.heading("added_by", text="Added By")
        
        self.tree.column("title", width=300)
        self.tree.column("artist", width=150)
        self.tree.column("added_by", width=100)
        
        self.tree.pack(side="top", fill="both", expand=True)
        
        # Bind Click for Checkboxes
        self.tree.bind("<Button-1>", self._on_tree_click)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(self.tree, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        # --- 4. Aero Toolbar (Bottom of Playlist) ---
        self.toolbar_frame = tk.Frame(self.playlist_panel, bg=BG_MAIN, height=50)
        self.toolbar_frame.pack(fill="x", pady=10)
        
        # Aero-styled buttons (Updated to match Play button)
        btn_style = {"font": ("Segoe UI", 9, "bold"), "relief": "raised", "bd": 2, "padx": 15, "pady": 5, "activebackground": ACCENT_HOVER}
        
        self.remove_btn = tk.Button(self.toolbar_frame, text="Remove Selected", bg=ACCENT, fg="#000000",
                                  state="disabled", command=self._handle_remove_checked, **btn_style)
        self.remove_btn.pack(side="left", padx=(0, 10))

        self.btn_clear_list = tk.Button(self.toolbar_frame, text="Clear Playlist", bg=ACCENT, fg="#000000",
                                      state="disabled", command=lambda: self._trigger(self.on_clear_queue), **btn_style)
        self.btn_clear_list.pack(side="left")

        # --- Debug Panel (Right Side - Initially Hidden) ---
        self.debug_panel = tk.Frame(self.middle_container, bg=TBG_TERM, width=300, bd=2, relief="sunken")
        
        tk.Label(self.debug_panel, text="SYSTEM TERMINAL", bg=TBG_TERM, fg=TEXT_TERM, 
                 font=("Consolas", 10, "bold")).pack(fill="x", pady=2)
        
        self.log_box = tk.Text(self.debug_panel, bg=BBG_TERM, fg=TEXT_TERM, font=FONT_MONO, 
                             borderwidth=0, state="disabled")
        self.log_box.pack(fill="both", expand=True, padx=2, pady=2)

    def _setup_player_ui(self):
        """Constructs the pinned player bar with a Deep Blue 'skin' aesthetic."""
        
        # A. Seek Bar Row
        seek_frame = tk.Frame(self.player_frame, bg=BG_PLAYER, pady=5)
        seek_frame.pack(fill="x", padx=PAD_L)
        
        self.lbl_current_time = tk.Label(seek_frame, text="0:00", bg=BG_PLAYER, fg=ACCENT, font=FONT_SMALL)
        self.lbl_current_time.pack(side="left")
        
        self.seek_slider = ttk.Scale(seek_frame, from_=0, to=100, orient="horizontal", command=self._handle_seek)
        self.seek_slider.pack(side="left", fill="x", expand=True, padx=PAD_M)
        
        self.lbl_total_time = tk.Label(seek_frame, text="--:--", bg=BG_PLAYER, fg=TEXT_SUB, font=FONT_SMALL)
        self.lbl_total_time.pack(side="right")

        # B. Controls Row
        controls_frame = tk.Frame(self.player_frame, bg=BG_PLAYER, pady=10)
        controls_frame.pack(fill="x", padx=PAD_L, pady=(0, 10))

        # B1. Song Info (Left)
        info_sub = tk.Frame(controls_frame, bg=BG_PLAYER, width=220)
        info_sub.pack(side="left", fill="y")
        info_sub.pack_propagate(False)
        
        self.now_playing_title = tk.Label(info_sub, text="Waiting for Host...", bg=BG_PLAYER, fg=ACCENT, font=("Segoe UI", 11, "bold"), anchor="w")
        self.now_playing_title.pack(fill="x")
        self.now_playing_artist = tk.Label(info_sub, text="--", bg=BG_PLAYER, fg=TEXT_MAIN, font=FONT_SMALL, anchor="w")
        self.now_playing_artist.pack(fill="x")

        # B2. Playback Controls (Center)
        self.host_controls = tk.Frame(controls_frame, bg=BG_PLAYER)
        self.host_controls.pack(side="left", expand=True)
        
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

        # B3. Volume (Right)
        tools_sub = tk.Frame(controls_frame, bg=BG_PLAYER)
        tools_sub.pack(side="right")
        
        tk.Label(tools_sub, text="🔊", bg=BG_PLAYER, fg=ACCENT).pack(side="left", padx=2)
        self.vol_slider = ttk.Scale(tools_sub, from_=0, to=100, orient="horizontal", command=self._handle_volume)
        self.vol_slider.set(70)
        self.vol_slider.pack(side="left", padx=5)

    # --- Interaction Logic ---

    def _on_tree_click(self, event):
        """Handle clicks on the checkbox column."""
        region = self.tree.identify_region(event.x, event.y)
        if region == "cell":
            col = self.tree.identify_column(event.x)
            # Column #1 is the 'select' column
            if col == "#1":
                item_id = self.tree.identify_row(event.y)
                if item_id:
                    current_values = self.tree.item(item_id, "values")
                    # Toggle between box and check
                    current_status = current_values[0]
                    new_status = "☑" if current_status == "☐" else "☐"
                    
                    # Update Treeview value
                    new_values = (new_status,) + current_values[1:]
                    self.tree.item(item_id, values=new_values)
                    
                    # Enable remove button if any are checked
                    self._check_selection_state()

    def _check_selection_state(self):
        """Enables remove button if checked items exist and is host."""
        if not self.controls_visible:
            return

        has_checked = False
        for child in self.tree.get_children():
            if self.tree.item(child, "values")[0] == "☑":
                has_checked = True
                break
        
        state = "normal" if has_checked else "disabled"
        self.remove_btn.config(state=state)

    def _handle_remove_checked(self):
        """Iterates through checked items and calls remove callback."""
        if not self.on_remove_song: return
        
        items_to_remove = []
        for child in self.tree.get_children():
            val = self.tree.item(child, "values")
            if val[0] == "☑":
                # Tuple structure: (Check, Title, Artist, AddedBy)
                # Backend expects (Title, Artist, AddedBy) or ID. 
                # Adapting to pass the relevant data part.
                song_data = (val[1], val[2], val[3]) 
                items_to_remove.append(song_data)
        
        for song_data in items_to_remove:
            self.on_remove_song(song_data)

    def set_controls_visible(self, is_host, host_id=None):
        self.controls_visible = is_host
        state = "normal" if is_host else "disabled"
        
        # Disable/Enable player controls
        for widget in self.host_controls.winfo_children():
            widget.configure(state=state)
        self.seek_slider.configure(state=state)
        
        # Disable/Enable management buttons
        self.btn_clear_list.configure(state=state)
        # Remove button logic depends on selection + host status
        if not is_host:
            self.remove_btn.config(state="disabled")
        else:
            self._check_selection_state()
        
        # Update Role Text
        if is_host:
            role_text = f"HOST (You: {self.node_id})"
            fg_color = TEXT_HOST
        elif host_id:
            role_text = f"LISTENER (Host: {host_id})"
            fg_color = TEXT_HOST
        else:
            role_text = "Finding Host..."
            fg_color = ACCENT_WARNING

        # Apply bold yellow font for host ID
        font = ("Segoe UI", 9, "bold") if (is_host or host_id) else FONT_SMALL
        self.status_label.config(text=role_text, fg=fg_color, font=font)

    def _trigger(self, callback):
        if callback and self.controls_visible:
            callback()

    def _handle_seek(self, value):
        if self.on_seek and self.controls_visible:
            self.on_seek(float(value))

    def _handle_volume(self, value):
        if self.on_volume_change:
            self.on_volume_change(float(value) / 100.0)

    def _add_song_dialog(self):
        file_path = filedialog.askopenfilename(filetypes=[("Audio Files", "*.mp3 *.wav *.ogg")])
        if file_path:
            self.on_add_song(file_path)

    # --- UI Update Methods ---

    def update_now_playing(self, title, artist="Unknown"):
        self.now_playing_title.config(text=title if title else "Nothing Playing")
        self.now_playing_artist.config(text=artist)

    def update_playlist(self, songs):
        """Updates treeview only if data changes to prevent UI flickering/reset."""
        # 1. Capture current state to compare
        current_items = self.tree.get_children()
        
        # Build list of (Title, Artist, AddedBy) from current tree (ignoring check status)
        current_data = []
        for item_id in current_items:
            vals = self.tree.item(item_id, "values")
            # vals is ("☐"/"☑", Title, Artist, AddedBy)
            if vals:
                current_data.append((vals[1], vals[2], vals[3]))
            
        # Build list from incoming songs
        new_data = [(song.title, song.artist, song.added_by) for song in songs]
        
        # 2. Check for equality - if match, do not refresh to preserve checkbox state
        if current_data == new_data:
            return 
            
        # 3. If changed, rebuild (and optionally preserve checks if possible)
        # For simple UX, if the list structure changes, we accept a reset of checks
        # or we could try to map checked titles.
        checked_titles = set()
        for item_id in current_items:
            vals = self.tree.item(item_id, "values")
            if vals and vals[0] == "☑":
                checked_titles.add(vals[1])
        
        # Clear current list
        for i in self.tree.get_children():
            self.tree.delete(i)
        
        # Re-populate 
        for song in songs:
            # Restore check if title matches
            check_mark = "☑" if song.title in checked_titles else "☐"
            self.tree.insert("", "end", values=(check_mark, song.title, song.artist, song.added_by))
        
        self._check_selection_state()

    def log_message(self, message):
        self.msg_queue.put(message)

    def toggle_debug(self):
        if self.debug_visible:
            self.debug_panel.pack_forget()
            self.root.geometry("850x650")
            self.debug_btn.config(relief="flat", bg=BG_HEADER)
        else:
            self.debug_panel.pack(side="right", fill="both", padx=(0, 0), pady=0)
            self.root.geometry("1150x650") 
            self.debug_btn.config(relief="sunken", bg=BG_PANEL)
            self.log_box.see("end")
        self.debug_visible = not self.debug_visible

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