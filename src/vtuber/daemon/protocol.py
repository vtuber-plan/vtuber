# src/vtuber/daemon/protocol.py
"""JSON message protocol for daemon-client communication."""
import json
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    """All message types in the daemon-provider protocol."""

    # Provider → Gateway
    REGISTER = "register"
    USER_MESSAGE = "user_message"
    PING = "ping"
    RELOAD = "reload"

    # Gateway → Provider
    ASSISTANT_MESSAGE = "assistant_message"
    TASK_MESSAGE = "task_message"
    HEARTBEAT_MESSAGE = "heartbeat_message"
    PROGRESS = "progress"
    ERROR = "error"
    PONG = "pong"


def encode_message(msg: dict[str, Any]) -> str:
    """Encode a message dict to JSON string with newline delimiter."""
    return json.dumps(msg, ensure_ascii=False) + "\n"


def decode_message(data: str) -> dict[str, Any]:
    """Decode a JSON string to message dict."""
    return json.loads(data.strip())
