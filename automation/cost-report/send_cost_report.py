#!/usr/bin/env python3
"""Daily LiteLLM cost report DM (agent-side half of W1-7).

Registered as a Hermes cron job (no_agent script mode, 09:00 KST) under the
`agent` account; the deployed copy lives at ~/.hermes/scripts/ (Hermes cron
sandbox rule). It can also be force-run directly:

    sudo -u agent -H python3 ~/.hermes/scripts/send_cost_report.py

Data path (account isolation): the agent NEVER touches ops secrets. It fetches
a MASKED spend aggregate over a command-restricted SSH forced command
(~agent/.ssh/autophagy-spend-ro -> ops@127.0.0.1, forced command =
automation/cost-report/spend_snapshot.py). Full spend detail stays in
/srv/autophagy-private/runtime-logs/ (ops-only).

Delivery: Discord DM to the owner, sent by the Autophagy-Agent bot
(DISCORD_BOT_TOKEN from ~agent/.env.secrets; owner_id from
~/.hermes/interop/config.json — the exact pattern proven in
automation/interop/gate_driver.py).

Soft cap: when month-to-date spend exceeds the required
`COST_REPORT_SOFT_CAP`, the report includes an alert line.

no_agent semantics: empty stdout + exit 0 on success (silent tick); on failure
prints one masked line and exits 1 so the scheduler records an error alert.

Test hooks (never set on the production cron path):
  COST_REPORT_SNAPSHOT_FILE  read snapshot JSON from a file instead of SSH
  COST_REPORT_DRY_RUN=1      print the composed body instead of sending the DM
  COST_REPORT_SOFT_CAP       required alert threshold in USD
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

DISCORD_API = "https://discord.com/api/v10"
SSH_IDENTITY = Path.home() / ".ssh" / "autophagy-spend-ro"
INTEROP_CONFIG = Path.home() / ".hermes" / "interop" / "config.json"
ENV_SECRETS = Path.home() / ".env.secrets"
SNAPSHOT_MAX_AGE_SECONDS = 600

REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_-]{6,}"), "[MASKED_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[MASKED_TOKEN]"),
    (re.compile(r"\b(?:Bearer|Bot)\s+\S+", re.IGNORECASE), "[MASKED_AUTH]"),
    (re.compile(r"\b[A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}\b"), "[MASKED_TOKEN]"),
    (re.compile(r"\b\d{17,19}\b"), "[MASKED_ID]"),
)


def redact(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def bot_token() -> str:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if token:
        return token
    for line in ENV_SECRETS.read_text().splitlines():
        if line.startswith("DISCORD_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("DISCORD_BOT_TOKEN not available")


def parse_json_object(raw: str, what: str) -> dict[str, object]:
    parsed: object = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{what} is not a JSON object")
    return parsed


def fetch_snapshot() -> dict[str, object]:
    override = os.environ.get("COST_REPORT_SNAPSHOT_FILE", "")
    if override:
        return parse_json_object(Path(override).read_text(), "snapshot file")
    result = subprocess.run(
        [
            "ssh",
            "-i", str(SSH_IDENTITY),
            "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=15",
            "ops@127.0.0.1",
            "cost-report",
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode != 0:
        raise RuntimeError(f"spend fetch failed rc={result.returncode}: {redact(result.stderr.strip())[:200]}")
    return parse_json_object(result.stdout, "spend snapshot")


def _num(value: object, what: str) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise RuntimeError(f"snapshot field {what} is not numeric")


def _stats(snapshot: dict[str, object], name: str) -> tuple[float, int]:
    section = snapshot.get(name)
    if not isinstance(section, dict):
        raise RuntimeError(f"snapshot field {name} malformed")
    return (
        _num(section.get("spend_usd"), f"{name}.spend_usd"),
        int(_num(section.get("requests"), f"{name}.requests")),
    )


def usd(value: float) -> str:
    return f"${value:.6f}"


def compose(snapshot: dict[str, object], soft_cap: float) -> str:
    kst_now = str(snapshot.get("kst_now", "?"))
    today_spend, today_requests = _stats(snapshot, "today_kst")
    month_spend, month_requests = _stats(snapshot, "month_to_date_kst")
    all_spend, all_requests = _stats(snapshot, "all_time")

    key_parts: list[str] = []
    entries = snapshot.get("per_key_month_kst")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            alias = str(entry.get("alias", "?"))
            spend = _num(entry.get("spend_usd"), "per_key.spend_usd")
            requests = int(_num(entry.get("requests"), "per_key.requests"))
            key_parts.append(f"{alias} {usd(spend)} ({requests}건)")
    lines = [
        f"📊 LiteLLM 일일 비용 리포트 — {kst_now} KST",
        f"오늘(KST) 지출: {usd(today_spend)} ({today_requests}건)",
        f"이번 달 누적: {usd(month_spend)} ({month_requests}건)",
        f"전체 누적: {usd(all_spend)} ({all_requests}건)",
        "키별(월): " + (", ".join(key_parts) if key_parts else "이번 달 사용 없음"),
    ]
    if month_spend > soft_cap:
        lines.append(
            f"⚠️ 소프트캡 경보: 월 누적 {usd(month_spend)} — ${soft_cap:g} 소프트캡 초과"
        )
    lines.append(f"(source: LiteLLM_SpendLogs · generated {snapshot.get('generated_at_utc', '?')})")
    return redact("\n".join(lines))


def discord_post(token: str, path: str, payload: dict[str, object]) -> dict[str, object]:
    request = Request(
        DISCORD_API + path,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)",
        },
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        raw = response.read().decode()  # pyright: ignore[reportAny] — http.client response
    return parse_json_object(raw, "discord response")


def send_dm(body: str) -> str:
    config = parse_json_object(INTEROP_CONFIG.read_text(), "interop config")
    owner_id = str(config["owner_id"])
    token = bot_token()
    channel = discord_post(token, "/users/@me/channels", {"recipient_id": owner_id})
    message = discord_post(token, f"/channels/{channel['id']}/messages", {"content": body})
    return str(message.get("id", ""))


def main() -> int:
    soft_cap = float(os.environ["COST_REPORT_SOFT_CAP"])
    snapshot = fetch_snapshot()
    body = compose(snapshot, soft_cap)
    if os.environ.get("COST_REPORT_DRY_RUN", "") == "1":
        print(body)
        return 0
    message_id = send_dm(body)
    # Success: stay silent (no_agent silent tick). Masked message id is logged
    # nowhere except the Hermes cron output store, and only in masked form.
    del message_id
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 — cron alert path: one masked line, nonzero exit
        print(f"cost-report error: {redact(str(error))[:300]}")
        sys.exit(1)
