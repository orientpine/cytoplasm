#!/usr/bin/env python3
"""W3-6 bank actuator (ops side) for the w3-report-hub scenario.

Runs ON ops@<primary-node>. Observes the W3-4 hub end-to-end for one synthetic
report already posted to #agents-log by the agent-side actuator:
  1. collect    poll reports.db (collector's 10s REST cadence) until the row
                for <message_id> lands (<=60s); observe its classification.
  2. dashboard  unauthenticated GET must be 401; authenticated GET (Basic
                credentials read from the ops-private credentials file, never
                printed) must be 200 and show the task id.
  3. cleanup    delete every W3-6-bank-% row (self-healing against crashed
                prior runs), verify absence, verify the collector watermark
                survived (so the deleted Discord message is never re-ingested).

Usage: w3_report_hub_ops.py <task_id> <message_id>
Emits masked progress + one OBS-JSON line.
"""
from __future__ import annotations

import base64
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DB = Path("/srv/autophagy-private/report-hub/reports.db")
CREDENTIALS = Path.home() / "report-hub" / "dashboard-cha-credentials.txt"
DASHBOARD = "http://100.116.248.95:8800/"
TASK_PREFIX = "W3-6-bank-"


def fetch_row(message_id: str) -> dict | None:
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        record = connection.execute(
            "SELECT * FROM reports WHERE message_id = ?", (message_id,)
        ).fetchone()
        return None if record is None else dict(record)
    finally:
        connection.close()


def http_status(url: str, header: dict[str, str] | None = None) -> tuple[int, str]:
    request = urllib.request.Request(url, headers=header or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, ""


def basic_auth_header() -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in CREDENTIALS.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip()
    token = base64.b64encode(f"{fields['user']}:{fields['password']}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def case_collect(message_id: str, task_id: str) -> dict:
    deadline = time.monotonic() + 60
    row = None
    while time.monotonic() < deadline:
        row = fetch_row(message_id)
        if row is not None:
            break
        time.sleep(3)
    if row is None:
        return {"db_row_within_60s": False, "error": "row not collected within 60s"}
    print(f"W36 collected msg_suffix={message_id[-4:]} note={row['registration_note']}")
    return {
        "db_row_within_60s": True,
        "db_agent_id": row["agent_id"],
        "db_task_match": row["task_id"] == task_id,
        "db_status": row["status"],
        "db_registered": row["registered"],
        "db_registration_note": row["registration_note"],
        "error": None,
    }


def case_dashboard(task_id: str) -> dict:
    unauth_status, _ = http_status(DASHBOARD)
    auth_status, body = http_status(DASHBOARD, basic_auth_header())
    return {
        "unauth_401": unauth_status == 401,
        "authed_200": auth_status == 200,
        "dashboard_shows_task": task_id in body,
        "error": None,
    }


def case_cleanup() -> dict:
    connection = sqlite3.connect(DB, timeout=30)
    try:
        watermark_before = connection.execute(
            "SELECT value FROM collector_state WHERE key = 'agents_log_watermark'"
        ).fetchone()
        deleted = connection.execute(
            "DELETE FROM reports WHERE task_id LIKE ?", (TASK_PREFIX + "%",)
        ).rowcount
        connection.commit()
        residual = connection.execute(
            "SELECT COUNT(*) FROM reports WHERE task_id LIKE ?", (TASK_PREFIX + "%",)
        ).fetchone()[0]
        watermark_after = connection.execute(
            "SELECT value FROM collector_state WHERE key = 'agents_log_watermark'"
        ).fetchone()
    finally:
        connection.close()
    print(f"W36 cleanup deleted_rows={deleted} residual={residual}")
    return {
        "bank_rows_deleted": deleted >= 1,
        "db_rows_absent_after": residual == 0,
        "watermark_preserved": bool(
            watermark_before and watermark_after and watermark_after[0] >= watermark_before[0]
        ),
        "error": None,
    }


def main() -> int:
    task_id, message_id = sys.argv[1], sys.argv[2]
    observations: dict[str, dict] = {}
    for case_id, runner in (
        ("collect", lambda: case_collect(message_id, task_id)),
        ("dashboard", lambda: case_dashboard(task_id)),
        ("cleanup", case_cleanup),
    ):
        try:
            observations[case_id] = runner()
        except Exception as error:  # noqa: BLE001 — observed, judged, isolated
            observations[case_id] = {"error": f"{type(error).__name__}: {error}"[:200]}
        print(f"W36 case={case_id} error={observations[case_id].get('error')}")
    print("OBS-JSON: " + json.dumps(observations, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
