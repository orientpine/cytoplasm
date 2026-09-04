"""MCP stdio client behavior against a deterministic local server."""

from __future__ import annotations

import sys

import pytest

from automation.plaud_sync.mcp_client import PlaudMcpClient, PlaudMcpError


FAKE_MCP_SERVER = r'''
import json
import sys

mode = sys.argv[1]
for raw_line in sys.stdin:
    message = json.loads(raw_line)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2025-03-26"}}), flush=True)
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [{"name": "list_notes"}, {"name": "get_note"}]}}), flush=True)
    elif method == "tools/call":
        if mode == "exit":
            print("server terminated unexpectedly", file=sys.stderr, flush=True)
            sys.exit(7)
        if mode == "timeout":
            continue
        if mode == "noise":
            print("unstructured server log", flush=True)
        tool_name = message["params"]["name"]
        if tool_name == "fail":
            result = {"isError": True, "content": [{"type": "text", "text": "tool failure detail"}]}
        else:
            result = {"content": [{"type": "text", "text": "completed"}], "structuredContent": {"name": tool_name, "arguments": message["params"]["arguments"]}}
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
'''


def _server_argv(mode: str = "normal") -> tuple[str, ...]:
    return (sys.executable, "-c", FAKE_MCP_SERVER, mode)


def test_handshake_and_list_tools_when_server_is_ready() -> None:
    # Given: a deterministic MCP server.
    with PlaudMcpClient(argv=_server_argv()) as client:
        # When: the initialized client lists its tools.
        tools = client.list_tools()

    # Then: the server's declared tool names are returned.
    assert tools == ("list_notes", "get_note")


def test_call_tool_returns_parsed_result_when_server_succeeds() -> None:
    # Given: a deterministic MCP server.
    with PlaudMcpClient(argv=_server_argv()) as client:
        # When: a tool is called with JSON arguments.
        result = client.call_tool("get_note", {"id": "note-1"})

    # Then: the complete parsed MCP result is available to the caller.
    assert result["structuredContent"] == {"name": "get_note", "arguments": {"id": "note-1"}}


def test_call_tool_raises_when_result_is_error() -> None:
    # Given: a server that reports a tool failure.
    with PlaudMcpClient(argv=_server_argv()) as client:
        # When: the failing tool is called.
        with pytest.raises(PlaudMcpError, match="tool failure detail"):
            client.call_tool("fail", {})

    # Then: the MCP error was surfaced rather than returned as success.


def test_call_tool_skips_non_json_stdout_noise_when_waiting_for_response() -> None:
    # Given: a server that leaks a non-JSON log line to stdout.
    with PlaudMcpClient(argv=_server_argv("noise")) as client:
        # When: a tool is called.
        result = client.call_tool("get_note", {})

    # Then: the valid JSON-RPC response is still returned.
    assert result["structuredContent"]["name"] == "get_note"


def test_call_tool_raises_when_server_does_not_answer_before_timeout() -> None:
    # Given: a server that deliberately does not answer tool calls.
    with PlaudMcpClient(argv=_server_argv("timeout")) as client:
        # When: the response wait reaches its bounded timeout.
        with pytest.raises(PlaudMcpError, match="timed out"):
            client.call_tool("get_note", {}, timeout=0.5)

    # Then: the timeout is reported as an MCP failure.


def test_call_tool_raises_with_stderr_tail_when_server_exits() -> None:
    # Given: a server that exits while handling a tool call.
    with PlaudMcpClient(argv=_server_argv("exit")) as client:
        # When: the client waits for the terminated server's response.
        with pytest.raises(PlaudMcpError, match="server terminated unexpectedly"):
            client.call_tool("get_note", {})

    # Then: the diagnostic stderr tail is preserved in the failure.
