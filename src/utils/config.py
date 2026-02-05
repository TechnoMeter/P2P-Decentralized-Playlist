import socket

# Networking Constants
UDP_PORT = 5000          # For UDP Peer Discovery
TCP_PORT = 5001          # For TCP State Sync
BUFFER_SIZE = 8192       # Standard buffer for object serialization

# Timing Constants (Seconds) - Optimized for faster failover
HEARTBEAT_INTERVAL = 1.0 
HOST_TIMEOUT = 3.1       # Miss ~3 heartbeats = Host Failure (Reduced from 6s)
ELECTION_TIMEOUT = 3.0   

# UI Theme (Lucrative Colors)
THEME = {
    "bg": "#121212",      # Deep dark background
    "primary": "#1DB954", # Spotify Green
    "secondary": "#212121",
    "text": "#FFFFFF",
    "accent": "#535353"
}

def get_local_ip():
    """Dynamically finds the local IP on the LAN."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"