"""Synchronous Plaud MCP facade; stdio transport lives in :mod:`mcp_transport`."""

from __future__ import annotations

import time
from collections.abc import Mapping
from types import TracebackType
from typing import Final, Self, assert_never

from .mcp_transport import (
    EndOfStream,
    JsonObject,
    JsonValue,
    PlaudMcpError as PlaudMcpError,
    ReaderFailure,
    Response,
    StdioMcpTransport,
)


DEFAULT_SERVER_ARGV: Final = ("npx", "-y", "@plaud-ai/mcp@0.3.10")
_INITIALIZE_TIMEOUT: Final = 60.0
_JSONRPC_VERSION: Final = "2.0"
_PROTOCOL_VERSION: Final = "2025-03-26"
_CLIENT_NAME: Final = "autophagy-plaud-sync"
_CLIENT_VERSION: Final = "0.1.0"

__all__ = (
    "DEFAULT_SERVER_ARGV",
    "JsonObject",
    "JsonValue",
    "PlaudMcpClient",
    "PlaudMcpError",
    "text_content",
)


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
    """A mutable protocol session that owns one initialized Plaud MCP server."""

    def __init__(
        self,
        argv: tuple[str, ...] = DEFAULT_SERVER_ARGV,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._transport: StdioMcpTransport = StdioMcpTransport(argv, env)
        self._next_id: int = 1

    def __enter__(self) -> Self:
        """Spawn and initialize the MCP server."""
        self._transport.start()
        try:
            _ = self._send_request(
                "initialize",
                {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": _CLIENT_NAME, "version": _CLIENT_VERSION},
                },
            )
            self._send_notification("notifications/initialized")
        except PlaudMcpError:
            self._shutdown()
            raise
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
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

    def call_tool(
        self, name: str, arguments: dict[str, JsonValue], timeout: float = 60.0
    ) -> JsonObject:
        """Call an MCP tool and return its parsed result."""
        return self._send_request("tools/call", {"name": name, "arguments": arguments}, timeout)

    def _send_request(
        self, method: str, params: JsonObject, timeout: float = _INITIALIZE_TIMEOUT
    ) -> JsonObject:
        request_id = self._next_id
        self._next_id += 1
        self._transport.send(
            {"jsonrpc": _JSONRPC_VERSION, "id": request_id, "method": method, "params": params}
        )
        return self._wait_for_response(request_id, timeout)

    def _send_notification(self, method: str) -> None:
        self._transport.send({"jsonrpc": _JSONRPC_VERSION, "method": method})

    def _wait_for_response(self, request_id: int, timeout: float) -> JsonObject:
        if timeout <= 0:
            raise PlaudMcpError("MCP response timeout must be positive")
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PlaudMcpError(self._transport.with_stderr_tail("timed out waiting for MCP response"))
            incoming = self._transport.receive(remaining)
            if incoming is None:
                raise PlaudMcpError(self._transport.with_stderr_tail("timed out waiting for MCP response"))

            match incoming:
                case Response(message=message):
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
                case ReaderFailure(error=error):
                    raise error
                case EndOfStream():
                    raise PlaudMcpError(
                        self._transport.with_stderr_tail("MCP server closed stdout before responding")
                    )
                case unreachable:
                    assert_never(unreachable)

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

    def _shutdown(self) -> None:
        self._transport.close()
