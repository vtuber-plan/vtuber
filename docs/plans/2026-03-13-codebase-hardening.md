# Codebase Hardening Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all identified bugs, race conditions, resource leaks, error handling gaps, and design flaws across the vtuber codebase.

**Architecture:** Systematic hardening pass organized by subsystem — each task is independent and can run in parallel. Changes are purely defensive (no new features, no API changes). All fixes preserve existing behavior.

**Tech Stack:** Python 3.12+, asyncio, Pydantic, claude_agent_sdk, websockets

---

## Chunk 1: Core Infrastructure Fixes

### Task 1: Add `AgentPool.owns()` method (CRITICAL — runtime crash)

`server.py:357` calls `self.agent_pool.owns(session_id, agent)` but this method doesn't exist.

**Files:**
- Modify: `src/vtuber/daemon/agents.py:171-237`

- [ ] **Step 1: Add `owns()` method to AgentPool**

In `src/vtuber/daemon/agents.py`, add after line 218 (after `close_all`):

```python
def owns(self, session_id: str, agent: ClaudeSDKClient) -> bool:
    """Check if the pool still holds *agent* for *session_id*."""
    return self._agents.get(session_id) is agent
```

- [ ] **Step 2: Verify no import/syntax errors**

Run: `python -c "from vtuber.daemon.agents import AgentPool; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/daemon/agents.py
git commit -m "fix: add AgentPool.owns() to prevent AttributeError at runtime"
```

---

### Task 2: Atomic session writes (CRITICAL — data loss risk)

`session.py:112` opens with `"w"` mode which truncates immediately. If the process crashes mid-write, the session file is destroyed.

**Files:**
- Modify: `src/vtuber/session.py:108-125`

- [ ] **Step 1: Implement atomic write with tempfile**

Replace the `save` method in `SessionManager`:

```python
def save(self, session: Session) -> None:
    """Save a session to disk atomically."""
    import tempfile

    path = self._get_session_path(session.key)

    metadata_line = {
        "_type": "metadata",
        "key": session.key,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "metadata": session.metadata,
        "last_consolidated": session.last_consolidated,
    }

    # Write to temp file in the same directory, then atomic rename
    fd, tmp_path = tempfile.mkstemp(
        dir=self.sessions_dir, suffix=".tmp", prefix=".session-",
    )
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
            for msg in session.messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
            f.flush()
        Path(tmp_path).replace(path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise

    self._cache[session.key] = session
```

- [ ] **Step 2: Add `import tempfile` to the file's imports** (it's used inline, already handled above)

- [ ] **Step 3: Verify**

Run: `python -c "from vtuber.session import SessionManager; print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add src/vtuber/session.py
git commit -m "fix: atomic session writes to prevent data loss on crash"
```

---

### Task 3: Fix config loading — log errors instead of swallowing

`config/model.py:143` has `except Exception: pass` — config parse errors are invisible.

**Files:**
- Modify: `src/vtuber/config/model.py:135-145`

- [ ] **Step 1: Add logging import and fix error handling**

Add `import logging` and `logger = logging.getLogger("vtuber.config")` at the top of `model.py` (after existing imports).

Then replace the `load_config` function:

```python
def load_config() -> VTuberConfig:
    """Load config from ~/.vtuber/config.yaml, falling back to defaults."""
    config_path = get_config_path()
    if config_path.exists():
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return VTuberConfig(**raw)
        except Exception as e:
            logger.warning("Failed to load config from %s: %s — using defaults", config_path, e)
    return VTuberConfig()
```

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.config.model import load_config; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/config/model.py
git commit -m "fix: log config parse errors instead of silently swallowing them"
```

---

### Task 4: Fix silent exception swallowing in Provider._read_loop

`providers/base.py:138` has `except Exception: pass` which hides all message dispatch errors.

**Files:**
- Modify: `src/vtuber/providers/base.py:134-139`

- [ ] **Step 1: Replace bare except with logging**

Replace in `_read_loop`:

```python
                    if line.strip():
                        try:
                            msg = decode_message(line)
                            await self._dispatch(msg)
                        except Exception:
                            pass
```

with:

```python
                    if line.strip():
                        try:
                            msg = decode_message(line)
                            await self._dispatch(msg)
                        except json.JSONDecodeError as e:
                            logger.debug("Invalid JSON from daemon: %s", e)
                        except Exception as e:
                            logger.error("Error dispatching message: %s", e, exc_info=True)
```

Also add `import json` to the imports at the top of the file (if not present).

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.providers.base import Provider; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/providers/base.py
git commit -m "fix: log message dispatch errors instead of silently swallowing them"
```

---

### Task 5: Socket resource leak in main.py

`main.py:22-34` — socket is not in a `finally` block. Early returns or exceptions leak the socket.

**Files:**
- Modify: `src/vtuber/main.py:12-50`

- [ ] **Step 1: Wrap socket in try/finally**

Replace the `_reload_daemon` function body starting from the `try:` on line 21:

```python
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect(str(socket_path))
        try:
            msg = json.dumps({"type": "reload"}) + "\n"
            sock.sendall(msg.encode("utf-8"))

            data = b""
            while b"\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        finally:
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
```

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.main import _reload_daemon; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/main.py
git commit -m "fix: ensure socket is closed in all error paths"
```

---

### Task 6: Unbounded message buffer in DaemonServer (DoS vector)

`server.py:194-204` — if a client sends data without newlines, the buffer grows without limit.

**Files:**
- Modify: `src/vtuber/daemon/server.py:185-217`

- [ ] **Step 1: Add buffer size limit**

Add a constant near the top of `server.py` (after imports):

```python
_MAX_BUFFER_SIZE = 1024 * 1024  # 1 MiB max per-client message buffer
```

Then in `_handle_client`, after `buffer += data` (line 201), add a check:

```python
                    buffer += data
                    if len(buffer) > _MAX_BUFFER_SIZE:
                        logger.warning("Client buffer exceeded %d bytes, disconnecting", _MAX_BUFFER_SIZE)
                        break
```

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.daemon.server import DaemonServer; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/daemon/server.py
git commit -m "fix: cap per-client buffer at 1MiB to prevent DoS"
```

---

## Chunk 2: Resource Lifecycle & Cleanup

### Task 7: Session lock cleanup — prevent unbounded memory growth

`server.py:102,116-120` — `_session_locks` dict only grows, never shrinks.

**Files:**
- Modify: `src/vtuber/daemon/server.py:91-120`

- [ ] **Step 1: Replace dict with bounded lock cache**

Replace the `_session_locks` initialization and `_get_session_lock` method:

```python
# In __init__, replace:
#   self._session_locks: dict[str, asyncio.Lock] = {}
# with:
        self._session_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._max_session_locks = 200
```

Add `from collections import OrderedDict` to the imports at the top.

Replace `_get_session_lock`:

```python
    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create a per-session lock. Evicts oldest when over limit."""
        if session_id in self._session_locks:
            self._session_locks.move_to_end(session_id)
            return self._session_locks[session_id]
        lock = asyncio.Lock()
        self._session_locks[session_id] = lock
        while len(self._session_locks) > self._max_session_locks:
            self._session_locks.popitem(last=False)
        return lock
```

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.daemon.server import DaemonServer; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/daemon/server.py
git commit -m "fix: bound session lock cache to prevent memory leak"
```

---

### Task 8: Gateway.close_all — one failure should not prevent others from closing

`gateway.py:104-108` — if one `conn.close()` raises, remaining connections leak.

**Files:**
- Modify: `src/vtuber/daemon/gateway.py:104-108`

- [ ] **Step 1: Wrap each close in try/except**

Replace `close_all`:

```python
    async def close_all(self) -> None:
        """Close all connections."""
        for conn in list(self.connections.values()):
            try:
                await conn.close()
            except Exception as e:
                logger.debug("Error closing connection %s: %s", conn.info, e)
        self.connections.clear()
```

- [ ] **Step 2: Fix broadcast() to also call close()**

Replace lines 100-102 in `broadcast`:

```python
        for pid in disconnected:
            conn = self.connections.pop(pid, None)
            if conn:
                await conn.close()
                logger.info("Removed dead connection: %s", conn.info)
```

- [ ] **Step 3: Verify**

Run: `python -c "from vtuber.daemon.gateway import Gateway; print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add src/vtuber/daemon/gateway.py
git commit -m "fix: resilient gateway close_all and proper cleanup in broadcast"
```

---

### Task 9: Agent disconnect timeout — kill subprocess instead of leaking

`agents.py:163-168` — on timeout, the subprocess is left running.

**Files:**
- Modify: `src/vtuber/daemon/agents.py:163-168`

- [ ] **Step 1: Kill subprocess on disconnect failure**

Replace `safe_disconnect`:

```python
async def safe_disconnect(agent: ClaudeSDKClient, timeout: float = 5.0) -> None:
    """Disconnect an agent safely with a timeout. Kills subprocess on failure."""
    try:
        await asyncio.wait_for(agent.disconnect(), timeout=timeout)
    except Exception:
        from vtuber.daemon.agent_query import kill_agent_process
        kill_agent_process(agent)
```

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.daemon.agents import safe_disconnect; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/daemon/agents.py
git commit -m "fix: kill agent subprocess when disconnect times out"
```

---

### Task 10: Fix shutdown exception handling

`server.py:586-593` — `except Exception: pass` and `except BaseException: pass` hide cleanup failures.

**Files:**
- Modify: `src/vtuber/daemon/server.py:569-614`

- [ ] **Step 1: Replace bare excepts with logged exceptions**

Replace the shutdown method body:

```python
    async def shutdown(self):
        """Shutdown the daemon server gracefully."""
        if not self.is_running:
            return
        logger.info("Shutting down daemon...")
        self.is_running = False

        # Stop subsystems
        if self._heartbeat:
            await self._heartbeat.stop()
        if self._task_runner:
            await self._task_runner.stop()

        # Disconnect all agents
        if self.agent_pool:
            try:
                await self.agent_pool.close_all()
            except Exception as e:
                logger.warning("Error closing agent pool: %s", e)

        # Close provider connections
        try:
            await self.gateway.close_all()
        except Exception as e:
            logger.warning("Error closing gateway: %s", e)

        # Shutdown scheduler
        if self.scheduler:
            try:
                self.scheduler.shutdown()
            except Exception as e:
                logger.warning("Error shutting down scheduler: %s", e)

        # Close socket server
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        # Remove socket and PID files
        if self.socket_path.exists():
            self.socket_path.unlink()
        pid_path = get_pid_path()
        if pid_path.exists():
            pid_path.unlink()

        logger.info("Daemon shutdown complete")
```

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.daemon.server import DaemonServer; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/daemon/server.py
git commit -m "fix: log shutdown errors instead of silently swallowing them"
```

---

### Task 11: Daemon startup file handle leak

`cli.py:89-98` — `log_file` is opened for Popen then immediately closed, which may cause issues. Use `pass_fds` pattern properly or let subprocess inherit.

**Files:**
- Modify: `src/vtuber/daemon/cli.py:86-98`

- [ ] **Step 1: Fix file handle management**

Replace lines 86-99:

```python
    # Start daemon in background
    try:
        ensure_config_dir()
        log_path = get_log_path()

        with open(log_path, "a", encoding="utf-8") as log_file:
            proc = subprocess.Popen(
                [sys.executable, "-m", "vtuber.daemon.server"],
                stdout=log_file,
                stderr=log_file,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        # File handle is now safely closed after Popen inherits it
        print("Daemon started in background")
        print(f"Log: {log_path}")
```

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.daemon.cli import start_daemon_background; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/daemon/cli.py
git commit -m "fix: use context manager for log file in daemon startup"
```

---

### Task 12: Provider.connect() — cleanup on partial failure

`providers/base.py:43-63` — if `_send()` fails after connection, reader/writer leak.

**Files:**
- Modify: `src/vtuber/providers/base.py:43-63`

- [ ] **Step 1: Add cleanup on connection failure**

Replace `connect`:

```python
    async def connect(self) -> bool:
        """Connect to daemon and register as a provider."""
        if not self.socket_path.exists():
            return False
        try:
            self.reader, self.writer = await asyncio.open_unix_connection(
                str(self.socket_path)
            )
            self.running = True
            # Register with the gateway
            await self._send({
                "type": MessageType.REGISTER,
                "provider": self.provider_type,
                "provider_id": self.provider_id,
            })
            # Start background socket reader
            self._reader_task = asyncio.create_task(self._read_loop())
            return True
        except Exception as e:
            logger.error("Connection failed: %s", e)
            # Clean up partially-established connection
            self.running = False
            if self.writer:
                try:
                    self.writer.close()
                    await self.writer.wait_closed()
                except Exception:
                    pass
            self.reader = None
            self.writer = None
            return False
```

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.providers.base import Provider; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/providers/base.py
git commit -m "fix: clean up partial connection on Provider.connect() failure"
```

---

## Chunk 3: OneBot Provider Hardening

### Task 13: Fix deprecated `asyncio.get_event_loop()` and uncancelled future

`onebot/provider.py:167` uses deprecated `get_event_loop()` and line 185 doesn't cancel the future on timeout.

**Files:**
- Modify: `src/vtuber/providers/onebot/provider.py:151-186`

- [ ] **Step 1: Fix send_onebot_action**

Replace `send_onebot_action`:

```python
    async def send_onebot_action(
        self, action: str, params: dict, *, wait: bool = False, timeout: float = 10.0,
    ) -> dict | None:
        """Send an action request to OneBot.

        If *wait* is ``True``, block until the response is received (correlated
        by ``echo``).  Returns the full response dict, or ``None`` on timeout.
        """
        if not self._ws:
            return None
        self._action_echo += 1
        echo_str = str(self._action_echo)
        payload = {"action": action, "params": params, "echo": echo_str}

        future: asyncio.Future | None = None
        if wait:
            future = asyncio.get_running_loop().create_future()
            self._api_futures[echo_str] = future

        try:
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.error("Failed to send OneBot action %s: %s", action, e)
            if future:
                self._api_futures.pop(echo_str, None)
            return None

        if future:
            try:
                return await asyncio.wait_for(future, timeout=timeout)
            except TimeoutError:
                logger.warning("OneBot action %s timed out", action)
                return None
            finally:
                self._api_futures.pop(echo_str, None)
                if not future.done():
                    future.cancel()
        return None
```

- [ ] **Step 2: Remove unused `sys` import if present**

Check if `sys` is used elsewhere in the file. If only used in `main()`, keep it. Otherwise remove.

- [ ] **Step 3: Verify**

Run: `python -c "from vtuber.providers.onebot.provider import OneBotProvider; print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add src/vtuber/providers/onebot/provider.py
git commit -m "fix: use get_running_loop(), cancel future on timeout in OneBot action"
```

---

### Task 14: Fix future.set_result potential InvalidStateError

`onebot/events.py:23-28` — `future.set_result()` can raise if future is already cancelled.

**Files:**
- Modify: `src/vtuber/providers/onebot/events.py:22-28`

- [ ] **Step 1: Guard future.set_result**

Replace:

```python
    echo = event.get("echo")
    if echo and echo in provider._api_futures:
        future = provider._api_futures.pop(echo)
        if not future.done():
            future.set_result(event)
        return
```

with:

```python
    echo = event.get("echo")
    if echo and echo in provider._api_futures:
        future = provider._api_futures.pop(echo)
        if not future.done():
            try:
                future.set_result(event)
            except asyncio.InvalidStateError:
                pass
        return
```

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.providers.onebot.events import handle_onebot_event; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/providers/onebot/events.py
git commit -m "fix: guard future.set_result against InvalidStateError"
```

---

### Task 15: Guard sender_info against None

`onebot/events.py:88-93` — if `event["sender"]` is `None`, `.get()` calls raise `AttributeError`.

**Files:**
- Modify: `src/vtuber/providers/onebot/events.py:88-93`

- [ ] **Step 1: Add None guard**

Replace:

```python
    sender_info = event.get("sender", {})
```

with:

```python
    sender_info = event.get("sender") or {}
```

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.providers.onebot.events import handle_onebot_event; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/providers/onebot/events.py
git commit -m "fix: guard against None sender in OneBot events"
```

---

## Chunk 4: Robustness & Error Handling

### Task 16: Fix `_safe_filename` to sanitize more characters

`session.py:40-42` only replaces `:` and `/`. Many characters are unsafe for filenames.

**Files:**
- Modify: `src/vtuber/session.py:40-42`

- [ ] **Step 1: Improve filename sanitization**

Replace:

```python
def _safe_filename(key: str) -> str:
    """Convert session key to safe filename."""
    return key.replace(":", "_").replace("/", "_")
```

with:

```python
import re

def _safe_filename(key: str) -> str:
    """Convert session key to safe filename."""
    # Replace common separators with underscore, strip anything else unsafe
    safe = key.replace(":", "_").replace("/", "_")
    safe = re.sub(r'[^\w\-.]', '_', safe)
    # Prevent .. and leading dots
    safe = safe.strip(".").replace("..", "_")
    return safe or "unnamed"
```

Move `import re` to the top-level imports of the file.

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.session import _safe_filename; print(_safe_filename('test:key/with spaces'))"`
Expected: `test_key_with_spaces`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/session.py
git commit -m "fix: sanitize more unsafe characters in session filenames"
```

---

### Task 17: SessionManager — bound the in-memory cache

`session.py:51,67` — cache grows unbounded.

**Files:**
- Modify: `src/vtuber/session.py:45-68`

- [ ] **Step 1: Use OrderedDict with LRU eviction**

Replace the SessionManager class header and `get_or_create`:

```python
class SessionManager:
    """Manages conversation sessions stored as JSONL files."""

    _MAX_CACHE = 100

    def __init__(self, sessions_dir: Path):
        self.sessions_dir = sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._cache: OrderedDict[str, Session] = OrderedDict()

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = _safe_filename(key)
        return self.sessions_dir / f"{safe_key}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """Get an existing session or create a new one."""
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        self._cache[key] = session
        while len(self._cache) > self._MAX_CACHE:
            self._cache.popitem(last=False)
        return session
```

Add `from collections import OrderedDict` to the imports.

Also update the `save` method's last line to move the key to end:

```python
        self._cache[session.key] = session
        self._cache.move_to_end(session.key)
```

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.session import SessionManager; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/session.py
git commit -m "fix: bound session cache with LRU eviction to prevent memory leak"
```

---

### Task 18: Config migration — atomic write

`config/yaml_gen.py:90-145` — non-atomic write can corrupt config.

**Files:**
- Modify: `src/vtuber/config/yaml_gen.py`

- [ ] **Step 1: Find the write operation in `migrate_config` and make it atomic**

In the `migrate_config` function, find where it writes back to `config_path`. Replace the direct `config_path.write_text(...)` with an atomic pattern:

```python
    import tempfile

    buf = io.StringIO()
    ry.dump(user_data, buf)
    new_content = buf.getvalue()

    # Atomic write: temp file + rename
    fd, tmp_path = tempfile.mkstemp(
        dir=config_path.parent, suffix=".tmp", prefix=".config-",
    )
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(new_content)
            f.flush()
        Path(tmp_path).replace(config_path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise
```

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.config.yaml_gen import migrate_config; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/config/yaml_gen.py
git commit -m "fix: atomic config migration writes to prevent corruption"
```

---

### Task 19: Heartbeat file write error handling

`heartbeat.py:256,264` — no error handling for file writes.

**Files:**
- Modify: `src/vtuber/daemon/heartbeat.py:251-268`

- [ ] **Step 1: Add error handling around file operations**

Replace the file writing section in `_consolidate_session` (after `if not tool_args: return`):

```python
        if not tool_args:
            return

        # Write history entry
        if entry := tool_args.get("history_entry"):
            if not isinstance(entry, str):
                entry = json.dumps(entry, ensure_ascii=False)
            try:
                history_path = get_history_path()
                with open(history_path, "a", encoding="utf-8") as f:
                    f.write(entry.rstrip() + "\n\n")
            except OSError as e:
                logger.error("[consolidation] failed to write history: %s", e)

        # Update long-term memory
        if update := tool_args.get("memory_update"):
            if not isinstance(update, str):
                update = json.dumps(update, ensure_ascii=False)
            if update != current_memory:
                try:
                    memory_path.write_text(update, encoding="utf-8")
                except OSError as e:
                    logger.error("[consolidation] failed to write memory: %s", e)

        # Update session metadata
        session.last_consolidated = len(session.messages) - keep_count
        manager.save(session)

        logger.info("[consolidation] session %s: consolidated up to message %d", session.key, session.last_consolidated)
```

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.daemon.heartbeat import HeartbeatManager; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/daemon/heartbeat.py
git commit -m "fix: add error handling for heartbeat file writes"
```

---

### Task 20: Scheduler shutdown — wait for running tasks

`scheduler.py:19-21` — `shutdown(wait=False)` abruptly kills running tasks.

**Files:**
- Modify: `src/vtuber/daemon/scheduler.py:19-21`

- [ ] **Step 1: Change to graceful shutdown with timeout**

Replace:

```python
    def shutdown(self):
        """Shutdown the scheduler."""
        self.scheduler.shutdown(wait=False)
```

with:

```python
    def shutdown(self, wait: bool = True):
        """Shutdown the scheduler."""
        self.scheduler.shutdown(wait=wait)
```

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.daemon.scheduler import TaskScheduler; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/daemon/scheduler.py
git commit -m "fix: graceful scheduler shutdown waits for running tasks by default"
```

---

## Chunk 5: Provider._send silence & Permission symlink fix

### Task 21: Provider._send should raise on disconnected state

`providers/base.py:107-116` — silently returns when writer is None. Callers assume message was sent.

**Files:**
- Modify: `src/vtuber/providers/base.py:107-116`

- [ ] **Step 1: Log warning when writer is None**

Replace:

```python
    async def _send(self, msg: dict) -> None:
        """Send a raw message dict to daemon."""
        if not self.writer:
            return
```

with:

```python
    async def _send(self, msg: dict) -> None:
        """Send a raw message dict to daemon."""
        if not self.writer:
            logger.warning("Cannot send message: not connected to daemon")
            return
```

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.providers.base import Provider; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/providers/base.py
git commit -m "fix: warn when Provider._send called without active connection"
```

---

### Task 22: Permission handler — use lstat to avoid symlink bypass

`permissions.py:23-26` — `.resolve()` follows symlinks, allowing bypass.

**Files:**
- Modify: `src/vtuber/permissions.py:23-26`

- [ ] **Step 1: Check for symlink before resolving**

Replace `_is_path_allowed`:

```python
def _is_path_allowed(file_path: str, allowed_dirs: list[Path]) -> bool:
    """Check if *file_path* falls under any of the allowed directories."""
    target = Path(file_path).expanduser().resolve()
    # Reject if any component of the original path is a symlink pointing outside
    raw = Path(file_path).expanduser()
    if raw.is_symlink():
        # Resolved target must still be under allowed dirs
        pass  # continue to the normal check below
    return any(target == d or d in target.parents for d in allowed_dirs)
```

Actually, the current logic already resolves the path — the concern is that a symlink *inside* an allowed dir could point *outside*. The `.resolve()` already canonicalizes the path, so the check `d in target.parents` already uses the real (resolved) path. The real fix is to also resolve the raw path's parent chain to detect if the *resolved* target escapes:

```python
def _is_path_allowed(file_path: str, allowed_dirs: list[Path]) -> bool:
    """Check if *file_path* falls under any of the allowed directories.

    Resolves symlinks so that a link inside an allowed dir pointing
    outside is correctly rejected.
    """
    try:
        target = Path(file_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    return any(target == d or d in target.parents for d in allowed_dirs)
```

The key change is wrapping `.resolve()` in try/except to handle invalid/circular symlinks.

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.permissions import _is_path_allowed; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/permissions.py
git commit -m "fix: handle invalid paths in permission check, prevent resolve() crash"
```

---

### Task 23: Fix `_cmd_stop` accessing private `_agents` dict

`server.py:79` — accesses `server.agent_pool._agents` directly.

**Files:**
- Modify: `src/vtuber/daemon/agents.py` (add public accessor)
- Modify: `src/vtuber/daemon/server.py:77-88`

- [ ] **Step 1: Add `get_agent()` to AgentPool**

In `agents.py`, add after the `owns()` method:

```python
    def get_agent(self, session_id: str) -> ClaudeSDKClient | None:
        """Return the agent for *session_id* without creating one, or None."""
        return self._agents.get(session_id)
```

- [ ] **Step 2: Update `_cmd_stop` to use public API**

In `server.py`, replace:

```python
    agent = server.agent_pool._agents.get(session_id)
```

with:

```python
    agent = server.agent_pool.get_agent(session_id)
```

- [ ] **Step 3: Verify**

Run: `python -c "from vtuber.daemon.server import DaemonServer; print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add src/vtuber/daemon/agents.py src/vtuber/daemon/server.py
git commit -m "fix: use public AgentPool.get_agent() instead of accessing private _agents"
```

---

### Task 24: Add `json` import to providers/base.py

This is needed for the Task 4 fix (json.JSONDecodeError in _read_loop).

**Files:**
- Modify: `src/vtuber/providers/base.py`

- [ ] **Step 1: Add json import**

This should be done as part of Task 4. If Task 4 already added it, verify it's there. If not, add `import json` to the imports section.

- [ ] **Step 2: Verify**

Run: `python -c "import vtuber.providers.base; print('OK')"`

- [ ] **Step 3: Commit** (can be combined with Task 4's commit)

---

## Chunk 6: Signal Handler & Remaining Fixes

### Task 25: Fix signal handler — use call_soon_threadsafe pattern

`server.py:180-181` — `asyncio.create_task()` in a signal handler is unreliable.

**Files:**
- Modify: `src/vtuber/daemon/server.py:178-181`

- [ ] **Step 1: Use a safer shutdown trigger**

Replace:

```python
        # Setup signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))
```

with:

```python
        # Setup signal handlers — schedule shutdown via event to avoid
        # creating tasks directly inside the signal callback.
        self._shutdown_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._shutdown_event.set)
```

Then update `run_forever` to watch the event:

```python
    async def run_forever(self):
        """Run the server until shutdown."""
        await self.start()
        try:
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()
```

- [ ] **Step 2: Add `_shutdown_event` initialization in `__init__`**

In `__init__`, add:

```python
        self._shutdown_event: asyncio.Event | None = None
```

- [ ] **Step 3: Verify**

Run: `python -c "from vtuber.daemon.server import DaemonServer; print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add src/vtuber/daemon/server.py
git commit -m "fix: use asyncio.Event for signal-triggered shutdown instead of create_task"
```

---

### Task 26: Add `markdown` to dependencies

`onebot/render.py` imports `markdown` but it's not declared in `pyproject.toml`.

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Verify the import**

Run: `grep -n 'import markdown' src/vtuber/providers/onebot/render.py`

- [ ] **Step 2: Add to dependencies if missing**

Check if `markdown` is already in `pyproject.toml` dependencies. If missing, add it. If already present (it was in the earlier analysis as `"markdown>=3.5.0"`), verify and skip this task.

- [ ] **Step 3: Commit (if changed)**

```bash
git add pyproject.toml
git commit -m "fix: ensure markdown is declared as a dependency"
```

---

### Task 27: Session load error — use f-string consistently with logger

`session.py:105` uses f-string in logger.error which bypasses lazy formatting.

**Files:**
- Modify: `src/vtuber/session.py:104-106`

- [ ] **Step 1: Fix logger call**

Replace:

```python
            logger.error(f"Failed to load session {key}: {e}")
```

with:

```python
            logger.error("Failed to load session %s: %s", key, e)
```

- [ ] **Step 2: Commit**

```bash
git add src/vtuber/session.py
git commit -m "fix: use lazy logger formatting in session load error"
```

---

### Task 28: Remove redundant `(TimeoutError, asyncio.TimeoutError)` catches

`asyncio.TimeoutError` is an alias for built-in `TimeoutError` since Python 3.11.

**Files:**
- Modify: `src/vtuber/providers/onebot/provider.py` (already fixed in Task 13)
- Modify: `src/vtuber/daemon/agent_query.py:157`

- [ ] **Step 1: Fix agent_query.py**

Replace at line 157:

```python
    except asyncio.TimeoutError:
```

with:

```python
    except TimeoutError:
```

(This is already the built-in, no import needed.)

- [ ] **Step 2: Verify**

Run: `python -c "from vtuber.daemon.agent_query import iter_response; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/vtuber/daemon/agent_query.py
git commit -m "fix: use built-in TimeoutError (asyncio.TimeoutError is deprecated alias)"
```

---

### Task 29: Final verification — run all tests

- [ ] **Step 1: Run the full test suite**

Run: `cd /Users/lex/Codes/vtuber && python -m pytest tests/ -v`

- [ ] **Step 2: Fix any regressions**

If tests fail, fix the issue and re-run.

- [ ] **Step 3: Run import check on all modified modules**

```bash
python -c "
from vtuber.daemon.server import DaemonServer
from vtuber.daemon.agents import AgentPool
from vtuber.daemon.gateway import Gateway
from vtuber.daemon.heartbeat import HeartbeatManager
from vtuber.daemon.scheduler import TaskScheduler
from vtuber.session import SessionManager
from vtuber.config.model import load_config
from vtuber.providers.base import Provider
from vtuber.providers.onebot.provider import OneBotProvider
from vtuber.permissions import agent_permission_handler
from vtuber.main import main
print('All imports OK')
"
```
