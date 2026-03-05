# src/vtuber/daemon/protocol.py
"""JSON message protocol for daemon-client communication."""
import json
from typing import Any


def encode_message(msg: dict[str, Any]) -> str:
    """Encode a message dict to JSON string with newline delimiter."""
    return json.dumps(msg, ensure_ascii=False) + "\n"


def decode_message(data: str) -> dict[str, Any]:
    """Decode a JSON string to message dict."""
    return json.loads(data.strip())
