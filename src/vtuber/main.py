"""Command-line entry point for vtuber."""

import sys

from rich.console import Console

console = Console()

USAGE = """\
[bold]VTuber[/bold] — 数字生命助手

[bold]用法：[/bold] vtuber <command>

[bold]命令：[/bold]
  [green]start[/green]        启动 daemon（后台运行）
  [green]stop[/green]         停止 daemon
  [green]status[/green]       查看 daemon 状态
  [green]chat[/green]         连接 daemon 开始对话
  [green]mock-group[/green]   模拟群聊测试
  [green]restart[/green]      重启 daemon
"""


def main():
    """Main command router."""
    if len(sys.argv) < 2:
        console.print(USAGE)
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
    elif command == "mock-group":
        from vtuber.providers.mock_group import main as mock_main
        mock_main()
    elif command == "restart":
        from vtuber.daemon.server import stop_daemon, start_daemon_background
        stop_daemon()
        start_daemon_background()
    else:
        console.print(f"[red]未知命令：{command}[/red]")
        console.print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()
