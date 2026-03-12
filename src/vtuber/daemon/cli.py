"""Daemon CLI helpers — start/stop/status commands and logging setup."""

import asyncio
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler

from vtuber.config import (
    ensure_config_dir,
    get_config,
    get_log_path,
    get_pid_path,
    get_socket_path,
)


def setup_logging():
    """Configure logging to ~/.vtuber/daemon.log with rotation."""
    ensure_config_dir()
    log_path = get_log_path()

    handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root = logging.getLogger("vtuber")
    level = getattr(logging, get_config().log_level, logging.INFO)
    root.setLevel(level)
    root.addHandler(handler)

    # Also log to stderr when running in foreground
    if sys.stderr and sys.stderr.isatty():
        console_handler = RichHandler(
            rich_tracebacks=True,
            show_path=False,
            markup=True,
        )
        console_handler.setLevel(level)
        root.addHandler(console_handler)


def start_daemon_background():
    """Start the daemon in background mode."""
    import subprocess

    socket_path = get_socket_path()
    pid_path = get_pid_path()

    # Check if daemon is already running
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            os.kill(pid, 0)
            print(f"Daemon is already running (PID: {pid})")
            return
        except (OSError, ProcessLookupError):
            pid_path.unlink()
            if socket_path.exists():
                socket_path.unlink()

    # Run onboarding interactively before starting background daemon
    from vtuber.onboarding import check_and_run_onboarding

    try:
        onboarded = asyncio.run(check_and_run_onboarding())
        if onboarded:
            print("Onboarding completed")
    except Exception as e:
        print(f"Onboarding check failed: {e}")
        print("Continuing with default configuration...")
        from vtuber.onboarding import create_default_configs
        create_default_configs()

    # Start daemon in background
    try:
        ensure_config_dir()
        log_path = get_log_path()
        log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115

        subprocess.Popen(
            [sys.executable, "-m", "vtuber.daemon.server"],
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        log_file.close()
        print("Daemon started in background")
        print(f"Log: {log_path}")

        import time
        time.sleep(1)

        if pid_path.exists():
            pid = int(pid_path.read_text().strip())
            print(f"Daemon running with PID: {pid}")
        else:
            print("Warning: Daemon may have failed to start (no PID file)")

    except Exception as e:
        print(f"Error starting daemon: {e}")
        sys.exit(1)


def stop_daemon():
    """Stop the running daemon."""
    pid_path = get_pid_path()

    if not pid_path.exists():
        print("Daemon is not running (no PID file)")
        return

    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, signal.SIGTERM)

        import time
        for _ in range(10):
            try:
                os.kill(pid, 0)
                time.sleep(1)
            except ProcessLookupError:
                print(f"Daemon stopped (PID: {pid})")
                return

        print("Daemon did not stop gracefully, forcing...")
        os.kill(pid, signal.SIGKILL)
        print(f"Daemon killed (PID: {pid})")

    except ProcessLookupError:
        print("Daemon is not running (process not found)")
        pid_path.unlink()
    except Exception as e:
        print(f"Error stopping daemon: {e}")


def check_status():
    """Check if the daemon is running."""
    socket_path = get_socket_path()
    pid_path = get_pid_path()

    if not pid_path.exists():
        print("Daemon is not running (no PID file)")
        return False

    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)

        print(f"Daemon is running (PID: {pid})")
        print(f"Socket: {socket_path}")

        if socket_path.exists():
            print("Socket file exists")
            import socket as sock
            try:
                test_sock = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
                test_sock.connect(str(socket_path))
                test_sock.close()
                print("Socket connection: OK")
                return True
            except Exception as e:
                print(f"Socket connection: FAILED ({e})")
                return False
        else:
            print("Socket file: MISSING")
            return False

    except ProcessLookupError:
        print("Daemon is not running (process not found)")
        pid_path.unlink()
        return False
    except Exception as e:
        print(f"Error checking status: {e}")
        return False
