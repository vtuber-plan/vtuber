"""CLI client - thin wrapper around CLIProvider for backwards compatibility."""

from vtuber.providers.cli import CLIProvider, main

# Re-export for backwards compatibility
CLIClient = CLIProvider

__all__ = ["CLIClient", "CLIProvider", "main"]

if __name__ == "__main__":
    main()
