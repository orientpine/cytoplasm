"""Synchronous stdio client for the Plaud MCP server."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Final, Self, TypeAlias


DEFAULT_SERVER_ARGV: Final = ("npx", "-y", "@plaud-ai/mcp@0.3.10")
_INITIALIZE_TIMEOUT: Final = 60.0
_STDERR_TAIL_LENGTH: Final = 500
_JSONRPC_VERSION: Final = "2.0"
_PROTOCOL_VERSION: Final = "2025-03-26"
_CLIENT_NAME: Final = "autophagy-plaud-sync"
_CLIENT_VERSION: Final = "0.1.0"


JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]


class PlaudMcpError(RuntimeError):
    """Raised when the Plaud MCP server cannot complete a protocol operation."""


@dataclass(frozen=True, slots=True)
class _Response:
    message: JsonObject


@dataclass(frozen=True, slots=True)
class _ReaderFailure:
    error: PlaudMcpError


@dataclass(frozen=True, slots=True)
class _EndOfStream:
    pass


_Incoming: TypeAlias = _Response | _ReaderFailure | _EndOfStream


def text_content(result: JsonObject) -> str:
    """Join text entries in an MCP tool result's content list."""
    content = result.get("content")
    if not isinstance(content, list):
        return ""

    texts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        text = item.get("text")
        if item_type == "text" and isinstance(text, str):
            texts.append(text)
    return "\n".join(texts)


class PlaudMcpClient:
    """A mutable process session that owns one initialized Plaud MCP server."""

    def __init__(
        self,
        argv: tuple[str, ...] = DEFAULT_SERVER_ARGV,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._argv: tuple[str, ...] = argv
        self._extra_env: dict[str, str] = dict(env) if env is not None else {}
        self._process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[_Incoming] = queue.Queue()
        self._stderr_tail: str = ""
        self._stderr_lock: threading.Lock = threading.Lock()
        self._stderr_thread: threading.Thread | None = None
        self._next_id: int = 1

    def __enter__(self) -> Self:
        """Spawn and initialize the MCP server."""
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

        try:
            threading.Thread(target=self._read_stdout, daemon=True).start()
            self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
            self._stderr_thread.start()
            _ = self._send_request("initialize", {"protocolVersion": _PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": _CLIENT_NAME, "version": _CLIENT_VERSION}})
            self._send_notification("notifications/initialized")
        except PlaudMcpError:
            self._shutdown()
            raise
        return self

    def __exit__(self, _exc_type: type[BaseException] | None, _exc: BaseException | None, _traceback: TracebackType | None) -> None:
        """Terminate the owned server and close all process pipes."""
        self._shutdown()

    def list_tools(self) -> tuple[str, ...]:
        """Return the names advertised by the initialized MCP server."""
        result = self._send_request("tools/list", {})
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise PlaudMcpError("malformed tools/list response: tools is not a list")

        names: list[str] = []
        for tool in tools:
            if not isinstance(tool, dict):
                raise PlaudMcpError("malformed tools/list response: tool is not an object")
            name = tool.get("name")
            if not isinstance(name, str):
                raise PlaudMcpError("malformed tools/list response: tool name is not a string")
            names.append(name)
        return tuple(names)

    def call_tool(self, name: str, arguments: dict[str, JsonValue], timeout: float = 60.0) -> JsonObject:
        """Call an MCP tool and return its parsed result."""
        return self._send_request("tools/call", {"name": name, "arguments": arguments}, timeout)

    def _send_request(self, method: str, params: JsonObject, timeout: float = _INITIALIZE_TIMEOUT) -> JsonObject:
        request_id = self._next_id
        self._next_id += 1
        self._write_message(
            {"jsonrpc": _JSONRPC_VERSION, "id": request_id, "method": method, "params": params}
        )
        return self._wait_for_response(request_id, timeout)

    def _send_notification(self, method: str) -> None:
        self._write_message({"jsonrpc": _JSONRPC_VERSION, "method": method})

    def _write_message(self, message: JsonObject) -> None:
        try:
            process = self._require_process()
            stdin = process.stdin
            if stdin is None:
                raise PlaudMcpError("MCP server stdin is unavailable")
            _ = stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise PlaudMcpError(self._with_stderr_tail(f"could not write to MCP server: {error}")) from error

    def _wait_for_response(self, request_id: int, timeout: float) -> JsonObject:
        if timeout <= 0:
            raise PlaudMcpError("MCP response timeout must be positive")
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PlaudMcpError(self._with_stderr_tail("timed out waiting for MCP response"))
            try:
                incoming = self._messages.get(timeout=remaining)
            except queue.Empty as error:
                raise PlaudMcpError(self._with_stderr_tail("timed out waiting for MCP response")) from error

            match incoming:
                case _Response(message=message):
                    response_id = message.get("id")
                    if response_id is None:
                        continue
                    if not isinstance(response_id, int) or isinstance(response_id, bool):
                        raise PlaudMcpError("malformed MCP response: id is not an integer")
                    if response_id == request_id:
                        return self._result_from_response(message)
                    if response_id != 0:
                        raise PlaudMcpError(
                            f"MCP response id mismatch: expected {request_id}, got {response_id}"
                        )
                case _ReaderFailure(error=error):
                    raise error
                case _EndOfStream():
                    raise PlaudMcpError(self._with_stderr_tail("MCP server closed stdout before responding"))

    def _result_from_response(self, response: JsonObject) -> JsonObject:
        error = response.get("error")
        if error is not None:
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                raise PlaudMcpError(error["message"])
            raise PlaudMcpError("MCP server returned an error response")

        result = response.get("result")
        if not isinstance(result, dict):
            raise PlaudMcpError("malformed MCP response: result is not an object")
        if bool(result.get("isError")):
            message = text_content(result)
            raise PlaudMcpError(message if message else "MCP tool returned an error")
        return result

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
                    decoded: JsonValue = json.loads(line)
                    parsed = _json_value(decoded)
                except json.JSONDecodeError as error:
                    if line.startswith("{"):
                        self._messages.put(_ReaderFailure(PlaudMcpError(f"malformed MCP JSON response: {error}")))
                        return
                    continue
                except PlaudMcpError as error:
                    self._messages.put(_ReaderFailure(error))
                    return
                if isinstance(parsed, dict):
                    self._messages.put(_Response(parsed))
        except ValueError:
            pass
        except OSError as error:
            self._messages.put(_ReaderFailure(PlaudMcpError(f"could not read MCP stdout: {error}")))
        finally:
            self._messages.put(_EndOfStream())

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

    def _with_stderr_tail(self, message: str) -> str:
        process = self._process
        if process is not None and process.poll() is not None and self._stderr_thread is not None:
            self._stderr_thread.join()
        with self._stderr_lock:
            tail = self._stderr_tail
        return f"{message}; stderr: {tail or '<empty>'}"

    def _shutdown(self) -> None:
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
