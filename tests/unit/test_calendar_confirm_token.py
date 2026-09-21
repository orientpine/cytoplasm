from __future__ import annotations

import sys
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from importlib import import_module
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "calendar" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

calendar_confirm = import_module("calendar_confirm")
calendar_gate = import_module("calendar_gate")
_REAL_API = calendar_confirm._api
from tests.unit.test_calendar_single_live_request import AGENT_CHAT_CHANNEL_ID  # noqa: E402
from tests.unit.test_calendar_confirm_message import (  # noqa: E402
    TITLE, START, END, START_DISPLAY, END_DISPLAY, _new_draft, calendar_approval,
    detail_flow as detail_flow, calendar_env as calendar_env,
)


def test_private_card_posts_through_real_http_when_destination_is_verified(
    detail_flow, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a loopback Discord endpoint; only the HTTP boundary is substituted.
    public_payloads: list[str] = []
    class Handler(BaseHTTPRequestHandler):
        def respond(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length)) if length else None
            reply = detail_flow(self.command, self.path, payload)
            if self.command == "POST" and (
                self.path.endswith("/threads") or self.path.endswith(f"/{AGENT_CHAT_CHANNEL_ID}/messages")
            ):
                public_payloads.append(json.dumps(payload, ensure_ascii=False))
            encoded = json.dumps(reply).encode() if reply is not None else b""
            self.send_response(200)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            _ = self.wfile.write(encoded)

        do_GET = respond
        do_POST = respond
        do_PUT = respond

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            monkeypatch.setattr(calendar_confirm, "API", f"http://127.0.0.1:{server.server_port}")
            monkeypatch.setattr(calendar_confirm, "_api", _REAL_API)
            monkeypatch.setenv("DISCORD_BOT_TOKEN", "offline-calendar-fixture")
            draft = _new_draft(["draft-create", "--text", "2031-10-01 09:00 meeting 1시간", "--summary", TITLE])
            # When: the real producer, directory, urllib transport and pending store run.
            entry = calendar_approval.request_confirmation(draft)
            # Then: exact frozen details reach the verified private card over HTTP.
            assert entry.render_version == "4"
            assert all(
                value in detail_flow.contents[entry.dm_message_id]
                for value in (TITLE, START_DISPLAY, END_DISPLAY)
            )
            assert calendar_approval.PendingConfirmStore().load() == (entry,)
            assert len(public_payloads) == 2
            assert all(value not in payload for payload in public_payloads for value in (TITLE, START, END))
        finally:
            server.shutdown()
            worker.join(timeout=5)
            assert not worker.is_alive()


def test_bot_token_prefers_environment_over_secrets_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    secrets = tmp_path / ".env.secrets"
    secrets.write_text("DISCORD_BOT_TOKEN=filetoken\n", encoding="utf-8")
    monkeypatch.setattr(calendar_confirm.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "envtoken")

    # When
    token = calendar_confirm.bot_token()

    # Then
    assert token == "envtoken"


def test_bot_token_reads_trimmed_value_from_secrets_file_when_environment_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    secrets = tmp_path / ".env.secrets"
    secrets.write_text(
        "# cron secrets\nOTHER_TOKEN=ignored\n DISCORD_BOT_TOKEN = ' filetoken ' \n",
        encoding="utf-8",
    )
    monkeypatch.setattr(calendar_confirm.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)

    # When
    token = calendar_confirm.bot_token()

    # Then
    assert token == "filetoken"


def test_bot_token_fails_closed_when_environment_and_secrets_file_are_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setattr(calendar_confirm.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)

    # When / Then
    with pytest.raises(calendar_gate.GateError):
        calendar_confirm.bot_token()
