"""Unix Domain Socket server for daemon."""
from pathlib import Path


class DaemonServer:
    """Unix Domain Socket server that manages client connections."""

    def __init__(self, socket_path: Path):
        self.socket_path = socket_path
        self.is_running = False
