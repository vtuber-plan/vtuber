"""Global memory tool - persistent key-value memory for the agent across conversations."""

import json
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

_MEMORY_DIR = Path.home() / ".vtuber" / "memory"


def _ensure_memory_dir() -> None:
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _memory_file() -> Path:
    return _MEMORY_DIR / "global.json"


def _load_memory() -> dict[str, Any]:
    _ensure_memory_dir()
    f = _memory_file()
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {}


def _save_memory(data: dict[str, Any]) -> None:
    _ensure_memory_dir()
    _memory_file().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@tool(
    "memorize",
    "Store a key-value pair in persistent global memory. Memory persists across conversations.",
    {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Memory key"},
            "value": {"type": "string", "description": "Value to remember"},
        },
        "required": ["key", "value"],
    },
)
async def memorize(args: dict[str, Any]) -> dict[str, Any]:
    mem = _load_memory()
    mem[args["key"]] = args["value"]
    _save_memory(mem)
    return {
        "content": [
            {"type": "text", "text": f"Memorized: {args['key']} = {args['value']}"}
        ]
    }


@tool(
    "recall",
    "Recall a value from persistent global memory by key, or list all memories if no key is given.",
    {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Memory key to recall. Omit to list all memories.",
            },
        },
        "required": [],
    },
)
async def recall(args: dict[str, Any]) -> dict[str, Any]:
    mem = _load_memory()
    key = args.get("key")

    if key is None:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(mem, ensure_ascii=False) if mem else "No memories stored.",
                }
            ]
        }

    value = mem.get(key)
    if value is None:
        return {
            "content": [{"type": "text", "text": f"No memory found for key: {key}"}]
        }
    return {"content": [{"type": "text", "text": f"{key} = {value}"}]}


@tool(
    "forget",
    "Remove a key from persistent global memory.",
    {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Memory key to forget"},
        },
        "required": ["key"],
    },
)
async def forget(args: dict[str, Any]) -> dict[str, Any]:
    mem = _load_memory()
    if args["key"] in mem:
        del mem[args["key"]]
        _save_memory(mem)
        return {
            "content": [{"type": "text", "text": f"Forgot: {args['key']}"}]
        }
    return {
        "content": [
            {"type": "text", "text": f"No memory found for key: {args['key']}"}
        ]
    }
