"""Command-line entry point for vtuber."""

import json
import socket
import sys

from rich.console import Console

console = Console()


def _reload_daemon():
    """Send a reload command to the running daemon."""
    from vtuber.config import get_socket_path

    socket_path = get_socket_path()
    if not socket_path.exists():
        console.print("[red]Daemon 未运行（socket 文件不存在）[/red]")
        sys.exit(1)

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect(str(socket_path))
        msg = json.dumps({"type": "reload"}) + "\n"
        sock.sendall(msg.encode("utf-8"))

        data = b""
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        sock.close()

        if data:
            resp = json.loads(data.decode("utf-8").strip())
            if resp.get("type") == "error":
                console.print(f"[red]Reload 失败：{resp.get('content')}[/red]")
                sys.exit(1)
            else:
                console.print("[green]Reload 成功 — 提示词已更新[/green]")
        else:
            console.print("[yellow]未收到 daemon 响应[/yellow]")
    except ConnectionRefusedError:
        console.print("[red]无法连接 daemon（连接被拒绝）[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Reload 失败：{e}[/red]")
        sys.exit(1)

USAGE = """\
[bold]VTuber[/bold] — 数字生命助手

[bold]用法：[/bold] vtuber <command>

[bold]命令：[/bold]
  [green]start[/green]        启动 daemon（后台运行）
  [green]stop[/green]         停止 daemon
  [green]status[/green]       查看 daemon 状态
  [green]chat[/green]         连接 daemon 开始对话
  [green]mock-group[/green]   模拟群聊测试
  [green]onebot[/green]       连接 OneBot v11 实现
  [green]restart[/green]      重启 daemon
  [green]reload[/green]       热重载提示词（无需重启）
"""


def main():
    """Main command router."""
    if len(sys.argv) < 2:
        console.print(USAGE)
        sys.exit(1)

    command = sys.argv[1]

    if command == "start":
        from vtuber.daemon.cli import start_daemon_background
        start_daemon_background()
    elif command == "stop":
        from vtuber.daemon.cli import stop_daemon
        stop_daemon()
    elif command == "status":
        from vtuber.daemon.cli import check_status
        check_status()
    elif command == "chat":
        from vtuber.client.cli import main as cli_main
        cli_main()
    elif command == "mock-group":
        from vtuber.providers.mock_group import main as mock_main
        mock_main()
    elif command == "onebot":
        from vtuber.providers.onebot import main as onebot_main
        onebot_main()
    elif command == "restart":
        from vtuber.daemon.cli import stop_daemon, start_daemon_background
        stop_daemon()
        start_daemon_background()
    elif command == "reload":
        _reload_daemon()
    else:
        console.print(f"[red]未知命令：{command}[/red]")
        console.print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()
