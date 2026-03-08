from vtuber.tools.memory import Session
from datetime import datetime


def test_session_creation():
    session = Session(key="cli:main")
    assert session.key == "cli:main"
    assert session.messages == []
    assert session.last_consolidated == 0


def test_session_add_message():
    session = Session(key="test:123")
    session.add_message("user", "Hello")
    assert len(session.messages) == 1
    assert session.messages[0]["role"] == "user"
    assert session.messages[0]["content"] == "Hello"
    assert "timestamp" in session.messages[0]
