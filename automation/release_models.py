"""릴리스 적용 통지에 싣는 모델 설정 요약 (2026-09-29 소유자 지시).

소유자가 명령을 따로 돌려야만 모델을 알 수 있으면 확인되지 않는다. 그래서 릴리스가 적용될 때마다
#notifications 통지에 agent·peer 의 주 모델·폴백·반복 한도(`agent.max_turns`)를 함께 싣는다.
워크스테이션이 운영자 권한(ssh + `sudo -n -u <계정>`)으로 두 계정의 `~/.hermes/config.yaml` 을
읽고, 요약만 JSON 으로 노드의 `send` 에 넘긴다 — agent 계정은 peer 홈을 읽을 수 없다.
읽지 못하면 통지는 그대로 나가고 모델 줄에 "확인 못 함"을 적는다(통지를 막지 않는다).
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Final

from automation import model_sync

HERMES_DEFAULT_MAX_TURNS: Final = "500"
_MARK: Final = "=== "


def _pairs(block: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for line in block.splitlines()[1:]:
        key, _, value = line.strip().lstrip("- ").partition(":")
        if value.strip():
            found.append((key.strip(), value.strip().strip("'\"")))
    return found


def _max_turns(text: str) -> str:
    inside = False
    for line in text.splitlines():
        if line[:1] not in ("", " ", "\t", "#", "-"):
            inside = line.split(":", 1)[0].strip() == "agent"
        elif inside and line.strip().startswith("max_turns:"):
            return line.split(":", 1)[1].strip()
    return HERMES_DEFAULT_MAX_TURNS


def summarize(text: str) -> dict[str, str]:
    """config.yaml 한 벌 → 주 모델·폴백·반복 한도."""
    blocks = model_sync.blocks(text)
    main = dict(_pairs(blocks.get("model", "")))
    routes: list[str] = []
    for key, value in _pairs(blocks.get("fallback_providers", "")):
        if key == "provider":
            routes.append(value)
        elif key == "model" and routes:
            routes[-1] = f"{routes[-1]}/{value}"
    return {
        "main": f"{main.get('provider', '?')}/{main.get('default', '?')}",
        "fallback": ", ".join(routes) or "없음",
        "max_turns": _max_turns(text),
    }


def parse_output(output: str) -> dict[str, dict[str, str]]:
    """`=== <계정>` 머리줄로 나뉜 설정 파일들 → 계정별 요약."""
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in output.splitlines():
        if line.startswith(_MARK):
            current = sections.setdefault(line[len(_MARK):].strip(), [])
        elif current is not None:
            current.append(line)
    return {account: summarize("\n".join(lines)) for account, lines in sections.items() if lines}


def _probe_command() -> list[str]:
    configured = os.environ.get("RELEASE_MODELS_PROBE_CMD", "").strip()
    if configured:
        return shlex.split(configured)
    from automation.node_config import load_node_config

    node = load_node_config()
    reads = "; ".join(
        f"echo '{_MARK}{role}'; sudo -n -u {account} -H cat {home}/.hermes/config.yaml"
        for role, account, home in (
            ("agent", node.agent_account, node.agent_home),
            ("peer", node.peer_account, node.peer_home),
        )
    )
    return ["ssh", node.deploy_ssh_host, reads]


def probe() -> str:
    """두 계정 요약의 JSON. 어떤 실패든 빈 문자열이다 — 통지를 막지 않는다."""
    try:
        done = subprocess.run(_probe_command(), capture_output=True, text=True, check=False, timeout=60)
        summaries = parse_output(done.stdout)
    except Exception:  # noqa: BLE001 - 모델 줄은 부가 정보라 어떤 실패도 통지를 막지 않는다
        return ""
    return json.dumps(summaries, ensure_ascii=False, sort_keys=True) if summaries else ""


def _line(summary: dict[str, str]) -> str:
    return f"주 {summary['main']} · 폴백 {summary['fallback']} · 반복 한도 {summary['max_turns']}"


def render(encoded: str) -> str:
    """통지 본문에 붙는 모델 줄. 요약이 없거나 깨졌으면 '확인 못 함'."""
    try:
        summaries = json.loads(encoded) if encoded else {}
        agent, peer = summaries.get("agent"), summaries.get("peer")
        rows = {"agent": _line(agent) if agent else None, "peer": _line(peer) if peer else None}
    except (ValueError, TypeError, KeyError, AttributeError):
        rows = {"agent": None, "peer": None}
    if rows["agent"] is None:
        return "모델: 확인 못 함 — 노드의 Hermes 설정을 읽지 못했다"
    if rows["agent"] == rows["peer"]:
        return f"모델: {rows['agent']} (agent·peer 같음)"
    lines = [f"모델(agent): {rows['agent']}", f"모델(peer): {rows['peer'] or '확인 못 함'}"]
    if rows["peer"] is not None:
        lines.append(
            "⚠️ peer 가 agent 와 다르다 — 모델은 python3 -m automation.model_sync --apply, "
            + "반복 한도는 각 계정 config.yaml 의 agent.max_turns"
        )
    return "\n".join(lines)
