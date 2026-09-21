"""Procurement CLI -> notice facade -> real multipart HTTP, with loopback only."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest


@pytest.mark.parametrize("status", [200, 403])
def test_cli_delivers_attachment_or_reports_failure_when_http_answers(
    tmp_path: Path, status: int,
) -> None:
    # Given a real document and a one-request loopback server, never Discord.
    root = Path(__file__).resolve().parents[2]
    scripts = root / "skills" / "procurement" / "scripts"
    draft = tmp_path / "draft.hwpx"
    original = b"\x00review-artifact\xff"
    _ = draft.write_bytes(original)
    received: list[tuple[str, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            size = int(self.headers["Content-Length"])
            content_type = self.headers["Content-Type"]
            received.append((self.path, content_type.encode() + b"\r\n\r\n" + self.rfile.read(size)))
            self.send_response(status)
            self.end_headers()
            _ = self.wfile.write(b'{"id":"notice-message"}')

    environment = {
        **os.environ,
        "HOME": str(tmp_path),
        "DISCORD_BOT_TOKEN": "unit-token",
        "OWNER_NOTICE_CHANNEL_ID": "notice-1",
        "DRIVE_PUBLISH_ENABLED": "0",
        "PROCURE_DISCORD_STUB": "",
        "PROCURE_DM_MAX_BYTES": "1024",
        "PROCURE_AUDIT_LOG": str(tmp_path / "audit.log"),
        "AUTOPHAGY_REPO_ROOT": str(root),
        "AUTOPHAGY_SKILL_LIVE_ROOT": str(tmp_path / "live"),
        "PYTHONPATH": os.pathsep.join((str(root), str(scripts))),
        "NO_PROXY": "127.0.0.1",
    }
    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 10
        receiver = Thread(target=server.handle_request, daemon=True)
        receiver.start()
        # When the real CLI runs in another process; only the API origin is injected.
        bootstrap = (
            "import sys; from automation import owner_notice; "
            "owner_notice._DISCORD_API = sys.argv.pop(1); "
            "from skills.procurement.scripts import procure_cli; "
            "procure_cli.main()"
        )
        try:
            completed = subprocess.run(
                [sys.executable, "-c", bootstrap, f"http://127.0.0.1:{server.server_port}",
                 "review", "--file", str(draft), "--note", "review"],
                cwd=root, env=environment, capture_output=True, text=True, timeout=15, check=False,
            )
        finally:
            receiver.join(timeout=12)
        assert not receiver.is_alive()

    # Then exactly one wire request carries the draft bytes and envelope.
    assert len(received) == 1
    path, wire = received[0]
    assert path == "/channels/notice-1/messages"
    multipart = BytesParser(policy=policy.default).parsebytes(b"Content-Type: " + wire)
    parts = tuple(multipart.iter_parts())
    assert len(parts) == 2
    assert parts[0].get_param("name", header="content-disposition") == "payload_json"
    payload = json.loads(parts[0].get_content())
    assert payload["attachments"] == [{"id": 0, "filename": draft.name}]
    assert draft.name in payload["content"]
    assert parts[1].get_param("name", header="content-disposition") == "files[0]"
    assert parts[1].get_filename() == draft.name
    assert parts[1].get_content() == original
    assert completed.returncode == (0 if status == 200 else 6), completed.stderr
    if status == 200:
        assert "REVIEW-DM-SENT message=notice mode=attach" in completed.stdout
    else:
        assert "NOTIFY-FAILED: HTTPError" in completed.stderr
        assert "REVIEW-FAILED" in completed.stderr
        assert "REVIEW-DM-SENT" not in completed.stdout
