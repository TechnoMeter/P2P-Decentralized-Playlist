from dataclasses import dataclass, field
import uuid
import time
from typing import Dict, Any, Optional

@dataclass
class Song:
    """Represents a music track in the decentralized queue."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = "Unknown Title"
    artist: str = "Unknown Artist"
    file_path: str = ""
    added_by: str = ""
    timestamp: float = field(default_factory=time.time)

@dataclass
class Message:
    """Standard envelope for all network traffic (Discovery, Election, Sync)."""
    sender_id: str
    sender_ip: str
    msg_type: str  # 'HELLO', 'ELECTION', 'COORDINATOR', 'HEARTBEAT', 'QUEUE_SYNC'
    payload: Any = None
    # Vector Clock for Causal Ordering
    vector_clock: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        if self.payload is None:
            self.payload = {}