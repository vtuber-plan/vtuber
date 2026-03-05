"""Command-line entry point for vtuber."""

import sys
from pathlib import Path


def main():
    """Main command router."""
    if len(sys.argv) < 2:
        print("Usage: vtuber <command> [args]")
        print("Commands: start, stop, status, chat, restart")
        sys.exit(1)

    command = sys.argv[1]

    if command == "start":
        from vtuber.daemon.server import start_daemon_background
        start_daemon_background()
    elif command == "stop":
        from vtuber.daemon.server import stop_daemon
        stop_daemon()
    elif command == "status":
        from vtuber.daemon.server import check_status
        check_status()
    elif command == "chat":
        from vtuber.client.cli import main as cli_main
        cli_main()
    elif command == "restart":
        from vtuber.daemon.server import stop_daemon, start_daemon_background
        stop_daemon()
        start_daemon_background()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
