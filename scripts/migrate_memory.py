#!/usr/bin/env python3
"""Migrate old memory files to new structure."""

import shutil
from pathlib import Path


def migrate():
    config_dir = Path.home() / ".vtuber"

    # Create memory directory
    memory_dir = config_dir / "memory"
    memory_dir.mkdir(exist_ok=True)

    # Migrate long_term_memory.md -> memory/MEMORY.md
    old_memory = config_dir / "long_term_memory.md"
    new_memory = memory_dir / "MEMORY.md"
    if old_memory.exists() and not new_memory.exists():
        shutil.move(str(old_memory), str(new_memory))
        print(f"✓ Migrated {old_memory} -> {new_memory}")

    # Migrate history.md -> memory/HISTORY.md
    old_history = config_dir / "history.md"
    new_history = memory_dir / "HISTORY.md"
    if old_history.exists() and not new_history.exists():
        shutil.move(str(old_history), str(new_history))
        print(f"✓ Migrated {old_history} -> {new_history}")

    # Remove consolidation state file (no longer needed)
    state_file = config_dir / "consolidation_state.json"
    if state_file.exists():
        state_file.unlink()
        print(f"✓ Removed obsolete {state_file}")

    # Sessions don't need migration (format change is too significant)
    # Old sessions can be manually reviewed if needed
    old_sessions = config_dir / "memory" / "sessions"
    if old_sessions.exists():
        backup = config_dir / "sessions_backup_old_format"
        if not backup.exists():
            shutil.move(str(old_sessions), str(backup))
            print(f"✓ Backed up old sessions to {backup}")
            print("  Old session format is incompatible. Backup preserved for manual review.")

    print("\n✅ Migration complete!")


if __name__ == "__main__":
    migrate()
