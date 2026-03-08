import tempfile
from pathlib import Path
from vtuber.tools.memory import SessionManager, Session


def test_session_manager_create():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = SessionManager(Path(tmpdir))
        session = manager.get_or_create("cli:main")
        assert session.key == "cli:main"
        assert session.messages == []


def test_session_manager_save_and_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = SessionManager(Path(tmpdir))

        # Create and save
        session = manager.get_or_create("test:123")
        session.add_message("user", "Hello")
        manager.save(session)

        # Clear cache and reload
        manager._cache.clear()
        loaded = manager.get_or_create("test:123")
        assert len(loaded.messages) == 1
        assert loaded.messages[0]["content"] == "Hello"


def test_session_manager_list():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = SessionManager(Path(tmpdir))

        session1 = manager.get_or_create("cli:main")
        session1.add_message("user", "Test")
        manager.save(session1)

        sessions = manager.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["key"] == "cli:main"
