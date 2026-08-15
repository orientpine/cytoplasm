#!/usr/bin/env python3
"""LiteLLM spend snapshot (ops-side half of the W1-7 daily cost report).

Runs as the configured ops account on the primary node. Queries the LiteLLM Postgres container
(`autophagy-litellm-postgres-1`, table "LiteLLM_SpendLogs") and:

  1. appends a FULL-detail JSON snapshot to
     /srv/autophagy-private/runtime-logs/cost-report/  (dir 700, file 600,
     ops-only — constraint 8: full detail never leaves the private log dir)
  2. prints a MASKED aggregate JSON to stdout.

The masked stdout is the ONLY thing the `agent` account can reach: it is
exposed through a command-restricted SSH forced command in
~ops/.ssh/authorized_keys (key: agent's dedicated `autophagy-spend-ro`
identity). The agent never sees ops secrets, the master key, api_key hashes,
or raw spend rows.

Masking policy (applied to every string in the masked output):
  - secret-shaped tokens (sk-*, gh?_*, Bearer/Bot values, base64-ish blobs)
  - Discord snowflake ids (17-19 digit runs)
  - key aliases outside the {agent, peer} whitelist become "other"

Test hooks (never used by the production cron path):
  COST_REPORT_FIXTURE   inject a sensitive-string fixture into the FULL
                        private snapshot; the masked stdout must not leak it.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TypeAlias

from automation.node_config import load_node_config

PRIVATE_DIR = Path("/srv/autophagy-private/runtime-logs/cost-report")
CONTAINER = "autophagy-litellm-postgres-1"
PSQL = ["docker", "exec", CONTAINER, "psql", "-U", "litellm", "-d", "litellm", "-At", "-F", "|"]
KST = "Asia/Seoul"
ALIAS_WHITELIST = {"agent", "peer"}

# "startTime" is `timestamp without time zone`, stored as UTC by LiteLLM.
KST_START = f"(\"startTime\" AT TIME ZONE 'UTC' AT TIME ZONE '{KST}')"

REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_-]{6,}"), "[MASKED_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[MASKED_TOKEN]"),
    (re.compile(r"\b(?:Bearer|Bot)\s+\S+", re.IGNORECASE), "[MASKED_AUTH]"),
    (re.compile(r"\b[A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}\b"), "[MASKED_TOKEN]"),
    (re.compile(r"\b\d{17,19}\b"), "[MASKED_ID]"),
)

JsonValue: TypeAlias = (
    "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"
)


def redact(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def redact_deep(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_deep(item) for item in value]
    if isinstance(value, dict):
        return {redact(str(k)): redact_deep(v) for k, v in value.items()}
    return value


def psql(sql: str) -> list[list[str]]:
    result = subprocess.run(PSQL + ["-c", sql], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"psql failed rc={result.returncode}: {redact(result.stderr.strip())}")
    rows = [line.split("|") for line in result.stdout.strip().splitlines() if line]
    return rows


def spend_window(where: str) -> dict[str, JsonValue]:
    rows = psql(
        'SELECT count(*)::bigint, COALESCE(SUM(spend),0)::float8 FROM "LiteLLM_SpendLogs"' + where
    )
    count, total = rows[0]
    return {"requests": int(count), "spend_usd": float(total)}


def per_alias(where: str) -> list[dict[str, JsonValue]]:
    rows = psql(
        "SELECT COALESCE(v.key_alias,'(no-alias)'), count(*)::bigint, "
        + "COALESCE(SUM(s.spend),0)::float8 "
        + 'FROM "LiteLLM_SpendLogs" s LEFT JOIN "LiteLLM_VerificationToken" v ON s.api_key = v.token'
        + where
        + " GROUP BY 1 ORDER BY 3 DESC"
    )
    return [
        {"alias": alias, "requests": int(count), "spend_usd": float(total)}
        for alias, count, total in rows
    ]


def build_snapshots() -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    today_where = f' WHERE {KST_START} >= date_trunc(\'day\', now() AT TIME ZONE \'{KST}\')'
    month_where = f' WHERE {KST_START} >= date_trunc(\'month\', now() AT TIME ZONE \'{KST}\')'
    kst_now = psql(f"SELECT to_char(now() AT TIME ZONE '{KST}', 'YYYY-MM-DD HH24:MI:SS')")[0][0]

    per_key_month = per_alias(month_where.replace('"startTime"', 's."startTime"'))
    per_key_month_json: list[JsonValue] = [entry for entry in per_key_month]
    per_key_all_time_json: list[JsonValue] = [entry for entry in per_alias("")]
    full: dict[str, JsonValue] = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kst_now": kst_now,
        "node": load_node_config().primary_node_name,
        "source": 'LiteLLM Postgres "LiteLLM_SpendLogs"',
        "all_time": spend_window(""),
        "today_kst": spend_window(today_where),
        "month_to_date_kst": spend_window(month_where),
        "per_key_month_kst": per_key_month_json,
        "per_key_all_time": per_key_all_time_json,
    }
    fixture = os.environ.get("COST_REPORT_FIXTURE", "")
    if fixture:
        full["fixture"] = fixture

    masked_aliases: list[JsonValue] = []
    for entry in per_key_month:
        alias_value = entry["alias"]
        alias = (
            alias_value
            if isinstance(alias_value, str) and alias_value in ALIAS_WHITELIST
            else "other"
        )
        masked_aliases.append(
            {"alias": alias, "requests": entry["requests"], "spend_usd": entry["spend_usd"]}
        )
    masked: dict[str, JsonValue] = {
        "generated_at_utc": full["generated_at_utc"],
        "kst_now": kst_now,
        "all_time": full["all_time"],
        "today_kst": full["today_kst"],
        "month_to_date_kst": full["month_to_date_kst"],
        "per_key_month_kst": masked_aliases,
    }
    redacted = redact_deep(masked)
    if not isinstance(redacted, dict):
        raise RuntimeError("redaction changed snapshot shape")
    return full, redacted


def write_private(full: dict[str, JsonValue]) -> Path:
    _ = os.umask(0o077)
    PRIVATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = os.chmod(PRIVATE_DIR, 0o700)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = PRIVATE_DIR / f"spend-{stamp}.json"
    _ = path.write_text(json.dumps(full, ensure_ascii=False, indent=2) + "\n")
    _ = os.chmod(path, 0o600)
    return path


def main() -> int:
    full, masked = build_snapshots()
    _ = write_private(full)
    print(json.dumps(masked, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 — forced command: one masked line, nonzero exit
        print(f"spend_snapshot error: {redact(str(error))}", file=sys.stderr)
        sys.exit(1)
