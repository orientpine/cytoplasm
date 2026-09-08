"""Stdio process and JSON framing used by :mod:`mcp_client` after its split."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, TypeAlias


_STDERR_TAIL_LENGTH: Final = 500

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]


class PlaudMcpError(RuntimeError):
    """Raised when the Plaud MCP server cannot complete a protocol operation."""


@dataclass(frozen=True, slots=True)
class Response:
    message: JsonObject


@dataclass(frozen=True, slots=True)
class ReaderFailure:
    error: PlaudMcpError


@dataclass(frozen=True, slots=True)
class EndOfStream:
    pass


_Response: TypeAlias = Response
_ReaderFailure: TypeAlias = ReaderFailure
_EndOfStream: TypeAlias = EndOfStream
_Incoming: TypeAlias = Response | ReaderFailure | EndOfStream


class StdioMcpTransport:
    """Mutable owner of one MCP stdio process and its reader threads."""

    def __init__(self, argv: tuple[str, ...], env: Mapping[str, str] | None) -> None:
        self._argv: tuple[str, ...] = argv
        self._extra_env: dict[str, str] = dict(env) if env is not None else {}
        self._process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[_Incoming] = queue.Queue()
        self._stderr_tail: str = ""
        self._stderr_lock: threading.Lock = threading.Lock()
        self._stderr_thread: threading.Thread | None = None

    def start(self) -> None:
        """Spawn the MCP process and begin draining its output streams."""
        environment = dict(os.environ)
        environment.update(self._extra_env)
        environment["PLAUD_TELEMETRY_DISABLED"] = "1"
        try:
            self._process = subprocess.Popen(
                self._argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=environment,
            )
        except OSError as error:
            raise PlaudMcpError(f"could not start Plaud MCP server: {error}") from error

        threading.Thread(target=self._read_stdout, daemon=True).start()
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()

    def send(self, message: JsonObject) -> None:
        """Serialize one JSON-RPC message onto the MCP process stdin."""
        try:
            process = self._require_process()
            stdin = process.stdin
            if stdin is None:
                raise PlaudMcpError("MCP server stdin is unavailable")
            _ = stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise PlaudMcpError(self.with_stderr_tail(f"could not write to MCP server: {error}")) from error

    def receive(self, timeout: float) -> _Incoming | None:
        """Return the next reader outcome, or ``None`` when the wait expires."""
        try:
            return self._messages.get(timeout=timeout)
        except queue.Empty:
            return None

    def with_stderr_tail(self, message: str) -> str:
        """Add the completed process's diagnostic tail to a failure message."""
        process = self._process
        if process is not None and process.poll() is not None and self._stderr_thread is not None:
            self._stderr_thread.join()
        with self._stderr_lock:
            tail = self._stderr_tail
        return f"{message}; stderr: {tail or '<empty>'}"

    def close(self) -> None:
        """Terminate the MCP process and close every owned pipe."""
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                _ = process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                _ = process.wait()
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        self._process = None

    def _read_stdout(self) -> None:
        try:
            process = self._require_process()
            stdout = process.stdout
            if stdout is None:
                raise PlaudMcpError("MCP server stdout is unavailable")
            for raw_line in stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    parsed = _json_value(json.loads(line))
                except json.JSONDecodeError as error:
                    if line.startswith("{"):
                        self._messages.put(ReaderFailure(PlaudMcpError(f"malformed MCP JSON response: {error}")))
                        return
                    continue
                except PlaudMcpError as error:
                    self._messages.put(ReaderFailure(error))
                    return
                if isinstance(parsed, dict):
                    self._messages.put(Response(parsed))
        except ValueError:
            return
        except OSError as error:
            self._messages.put(ReaderFailure(PlaudMcpError(f"could not read MCP stdout: {error}")))
        finally:
            self._messages.put(EndOfStream())

    def _read_stderr(self) -> None:
        try:
            process = self._require_process()
            stderr = process.stderr
            if stderr is None:
                raise PlaudMcpError("MCP server stderr is unavailable")
            for chunk in stderr:
                with self._stderr_lock:
                    self._stderr_tail = (self._stderr_tail + chunk)[-_STDERR_TAIL_LENGTH:]
        except OSError:
            return

    def _require_process(self) -> subprocess.Popen[str]:
        if self._process is None:
            raise PlaudMcpError("Plaud MCP client is not running")
        return self._process


def _json_value(value: JsonValue) -> JsonValue:
    """Normalize a decoded JSON value into the module's recursive JSON type."""
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        normalized: JsonObject = {}
        for key, item in value.items():
            normalized[key] = _json_value(item)
        return normalized
    return value
