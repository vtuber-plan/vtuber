"""Shared file-path detection for provider reply helpers."""

import json
from pathlib import Path

SENDABLE_EXTENSIONS = frozenset((
    ".pdf", ".markdown", ".md", ".txt",
    ".ppt", ".pptx", ".doc", ".docx",
    ".wav", ".mp3",
    ".jpg", ".jpeg", ".gif", ".png",
))


def parse_file_reply(text: str) -> list[Path]:
    """Parse a reply that consists entirely of a JSON array of absolute paths.

    Returns the validated file paths if the reply matches the expected
    format, otherwise returns an empty list.
    """
    stripped = text.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return []

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return []

    if not isinstance(parsed, list) or not parsed:
        return []
    if not all(isinstance(p, str) and p.startswith("/") for p in parsed):
        return []

    paths: list[Path] = []
    for raw in parsed:
        p = Path(raw)
        if p.is_file() and p.suffix.lower() in SENDABLE_EXTENSIONS:
            paths.append(p)

    # Only treat as file-upload if ALL entries are valid files
    if len(paths) != len(parsed):
        return []

    return paths
