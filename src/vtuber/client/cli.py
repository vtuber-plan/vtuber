"""CLI client for connecting to the VTuber daemon."""

import asyncio
import sys
from pathlib import Path

from vtuber.daemon.protocol import encode_message, decode_message
from vtuber.config import get_socket_path


class CLIClient:
    """Command-line client for interacting with the VTuber daemon."""

    def __init__(self, socket_path: Path | None = None):
        self.socket_path = socket_path or get_socket_path()
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.running = False

    async def connect(self):
        """Connect to the daemon server."""
        if not self.socket_path.exists():
            print(f"Error: Daemon socket not found at {self.socket_path}")
            print("Please start the daemon first with: vtuber start")
            return False

        try:
            self.reader, self.writer = await asyncio.open_unix_connection(
                str(self.socket_path)
            )
            self.running = True
            print(f"Connected to daemon at {self.socket_path}")
            print("Type your message and press Enter. Type /quit or /exit to quit.\n")
            return True
        except Exception as e:
            print(f"Error connecting to daemon: {e}")
            return False

    async def disconnect(self):
        """Disconnect from the daemon server."""
        self.running = False
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        print("\nDisconnected from daemon")

    async def send_message(self, content: str):
        """Send a user message to the daemon."""
        if not self.writer:
            return

        try:
            msg = encode_message({"type": "user_message", "content": content})
            self.writer.write(msg.encode("utf-8"))
            await self.writer.drain()
        except Exception as e:
            print(f"Error sending message: {e}")

    async def receive_messages(self):
        """Receive and display messages from the daemon."""
        if not self.reader:
            return

        buffer = ""
        try:
            while self.running:
                data = await self.reader.read(4096)
                if not data:
                    print("\nDaemon connection closed")
                    self.running = False
                    break

                buffer += data.decode("utf-8")

                # Process complete messages
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        await self._handle_message(line)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self.running:
                print(f"\nError receiving messages: {e}")

    async def _handle_message(self, line: str):
        """Handle a message from the daemon."""
        try:
            msg = decode_message(line)
            msg_type = msg.get("type")

            if msg_type == "assistant_message":
                # Stream assistant message
                content = msg.get("content", "")
                is_final = msg.get("is_final", False)

                # Print content without newline for streaming
                if content:
                    print(content, end="", flush=True)

                # Add newline after final message
                if is_final:
                    print()  # Newline after complete message

            elif msg_type == "task_message":
                # Scheduled task result
                content = msg.get("content", "")
                is_final = msg.get("is_final", False)
                task = msg.get("task", "")

                if content:
                    print(content, end="", flush=True)

                if is_final:
                    print()  # Newline after complete task message

            elif msg_type == "error":
                error = msg.get("content", "Unknown error")
                print(f"\nError: {error}")

            elif msg_type == "pong":
                # Ignore pong responses
                pass

            else:
                print(f"\nUnknown message type: {msg_type}")

        except Exception as e:
            print(f"\nError handling message: {e}")

    async def run(self):
        """Run the interactive CLI client."""
        if not await self.connect():
            return

        # Start receive task
        receive_task = asyncio.create_task(self.receive_messages())

        try:
            # Read user input
            while self.running:
                try:
                    # Read input in executor to avoid blocking
                    loop = asyncio.get_event_loop()
                    user_input = await loop.run_in_executor(None, input, "> ")

                    # Check for exit commands
                    if user_input.strip().lower() in ["/quit", "/exit"]:
                        break

                    # Send message if not empty
                    if user_input.strip():
                        await self.send_message(user_input)

                except EOFError:
                    # Ctrl+D
                    break
                except KeyboardInterrupt:
                    # Ctrl+C
                    print()
                    continue

        finally:
            # Cancel receive task and disconnect
            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass
            await self.disconnect()


def main():
    """Main entry point for CLI client."""
    try:
        client = CLIClient()
        asyncio.run(client.run())
    except KeyboardInterrupt:
        print("\n\nGoodbye!")
        sys.exit(0)
    except Exception as e:
        print(f"Client error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
