# tests/daemon/test_protocol.py
import pytest
from vtuber.daemon.protocol import encode_message, decode_message


def test_encode_user_message():
    msg = {"type": "user_message", "content": "Hello"}
    result = encode_message(msg)
    assert result == '{"type": "user_message", "content": "Hello"}\n'


def test_decode_user_message():
    data = '{"type": "user_message", "content": "Hello"}\n'
    result = decode_message(data)
    assert result == {"type": "user_message", "content": "Hello"}


def test_encode_with_stream_id():
    msg = {
        "type": "assistant_message",
        "stream_id": "abc123",
        "index": 0,
        "content": "Hi",
        "is_final": False,
    }
    result = encode_message(msg)
    assert '"stream_id": "abc123"' in result
    assert '"is_final": false' in result
