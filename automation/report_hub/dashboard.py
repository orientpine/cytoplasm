"""Report hub dashboard: tailnet-bound, Basic-auth-mandatory SQLite read view.

Fail-closed: a non-loopback bind refuses to start unless both the dashboard
user and the SHA-256 password hash are configured.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import html
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final
from urllib.parse import parse_qs, urlparse

from automation.report_hub.store import ReportQuery, ReportRow

_STATUSES: Final = ("start", "done", "blocked")
_REALM: Final = "autophagy-report-hub"

logger = logging.getLogger("report_hub.dashboard")

_PAGE_TEMPLATE: Final = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Autophagy Report Hub</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1c2733; }}
  h1 {{ font-size: 1.4rem; }}
  h2 {{ font-size: 1.05rem; margin-top: 2rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: .5rem; }}
  th, td {{ border: 1px solid #cdd6e0; padding: .4rem .6rem; text-align: left;
            font-size: .875rem; vertical-align: top; }}
  th {{ background: #eef2f6; }}
  .badge {{ padding: .1rem .45rem; border-radius: .6rem; font-size: .75rem; }}
  .registered {{ background: #d9f2e2; }}
  .unregistered {{ background: #fbe0e0; }}
  .status-done {{ background: #d9f2e2; }}
  .status-start {{ background: #dfeafc; }}
  .status-blocked {{ background: #fbe9d0; }}
  form {{ margin-top: 1rem; display: flex; gap: .8rem; align-items: center; }}
</style>
</head>
<body>
<h1>Autophagy Report Hub — #agents-log</h1>
<form method="get" action="/">
  <label>Agent
    <select name="agent">{agent_options}</select>
  </label>
  <label>Status
    <select name="status">{status_options}</select>
  </label>
  <button type="submit">Filter</button>
</form>
<h2>Per-agent</h2>
<table id="per-agent">{agent_counts}</table>
<h2>Per-status</h2>
<table id="per-status">{status_counts}</table>
<h2>Timeline ({row_count} reports)</h2>
<table id="reports">
<tr><th>Report time (UTC)</th><th>Agent</th><th>Task</th><th>Status</th>
<th>Summary</th><th>Registration</th><th>Links</th></tr>
{report_rows}
</table>
</body>
</html>
"""


def _options(values: list[str], selected: str) -> str:
    rendered = ['<option value="">(all)</option>']
    for value in values:
        flag = " selected" if value == selected else ""
        rendered.append(f'<option value="{html.escape(value, quote=True)}"{flag}>{html.escape(value)}</option>')
    return "".join(rendered)


def _count_rows(counts: list[tuple[str, int]], kind: str) -> str:
    header = f"<tr><th>{kind}</th><th>reports</th></tr>"
    body = "".join(
        f'<tr data-count-{kind}="{html.escape(name, quote=True)}"><td>{html.escape(name)}</td><td>{count}</td></tr>'
        for name, count in counts
    )
    return header + body


def _report_row(row: ReportRow) -> str:
    registration = "registered" if row.registered else "unregistered"
    links = " ".join(html.escape(link) for link in row.links)
    return (
        f'<tr class="report-row" data-agent="{html.escape(row.agent_id, quote=True)}"'
        f' data-status="{html.escape(row.status, quote=True)}" data-registered="{registration}">'
        f"<td>{html.escape(row.report_timestamp)}</td>"
        f"<td>{html.escape(row.agent_id)}</td>"
        f"<td>{html.escape(row.task_id)}</td>"
        f'<td><span class="badge status-{html.escape(row.status, quote=True)}">{html.escape(row.status)}</span></td>'
        f"<td>{html.escape(row.summary)}</td>"
        f'<td><span class="badge {registration}">{registration}'
        f"{'' if row.registration_note == 'registered' else ': ' + html.escape(row.registration_note)}</span></td>"
        f"<td>{links}</td></tr>"
    )


def render_page(query: ReportQuery, agent_filter: str, status_filter: str) -> str:
    """Build the full dashboard HTML for the given filters."""
    rows = query.reports(agent_id=agent_filter or None, status=status_filter or None)
    agents = [name for name, _ in query.counts_by_agent()]
    return _PAGE_TEMPLATE.format(
        agent_options=_options(sorted(agents), agent_filter),
        status_options=_options(list(_STATUSES), status_filter),
        agent_counts=_count_rows(query.counts_by_agent(), "agent"),
        status_counts=_count_rows(query.counts_by_status(), "status"),
        row_count=len(rows),
        report_rows="".join(_report_row(row) for row in rows),
    )


class DashboardHandler(BaseHTTPRequestHandler):
    """Basic-auth-gated read-only view over the reports database."""

    database_path: Path
    auth_user: str
    auth_password_sha256: str

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
            user, _, password = decoded.partition(":")
        except (binascii.Error, UnicodeDecodeError):
            return False
        digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(user, self.auth_user) and hmac.compare_digest(
            digest, self.auth_password_sha256
        )

    def _deny(self) -> None:
        self.send_response(401)
        self.send_header("WWW-Authenticate", f'Basic realm="{_REALM}"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"authentication required\n")

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if not self._authorized():
            self._deny()
            return
        parsed = urlparse(self.path)
        if parsed.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        parameters = parse_qs(parsed.query)
        query = ReportQuery(self.database_path)
        try:
            page = render_page(
                query,
                agent_filter=parameters.get("agent", [""])[0],
                status_filter=parameters.get("status", [""])[0],
            )
        finally:
            query.close()
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        logger.info("%s %s", self.address_string(), format % args)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    bind_host = os.environ.get("REPORT_HUB_BIND_HOST", "127.0.0.1")
    bind_port = int(os.environ.get("REPORT_HUB_BIND_PORT", "8800"))
    database_path = Path(os.environ.get("REPORT_HUB_DB", ""))
    auth_user = os.environ.get("REPORT_HUB_DASHBOARD_USER", "")
    auth_hash = os.environ.get("REPORT_HUB_DASHBOARD_PASSWORD_SHA256", "")

    if not database_path.name:
        raise SystemExit("REPORT_HUB_DB is required")
    if not auth_user or len(auth_hash) != 64:
        raise SystemExit(
            "mandatory auth is not configured (REPORT_HUB_DASHBOARD_USER +"
            " 64-hex REPORT_HUB_DASHBOARD_PASSWORD_SHA256); refusing to serve"
        )

    handler = type(
        "BoundDashboardHandler",
        (DashboardHandler,),
        {
            "database_path": database_path,
            "auth_user": auth_user,
            "auth_password_sha256": auth_hash.lower(),
        },
    )
    server = ThreadingHTTPServer((bind_host, bind_port), handler)
    logger.info("dashboard serving on %s:%s (auth mandatory)", bind_host, bind_port)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
