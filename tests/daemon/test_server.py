from pathlib import Path
from vtuber.daemon.server import DaemonServer


def test_server_creation(tmp_path):
    socket_path = tmp_path / "test.sock"
    server = DaemonServer(socket_path)
    assert server.socket_path == socket_path
    assert not server.is_running
