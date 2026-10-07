"""배포기 시험 리그가 공유하는 cron 선언 조회와 가짜 `hermes`(RCB todo 27).

배포기는 `converge_cron`(`automation/deploy_cron.sh`)으로 등록을 맞추므로, 배포기를 실제로 돌리는
리그는 그 헬퍼와 노드 실측 형식으로 목록을 내는 `hermes` 가 있어야 한다. 기대값은 리그마다 적지 않고
`deploy-manifest.txt` 의 `v2:cron` 선언에서 끌어온다.
"""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Final

from automation.deploy_declarations import Declaration, all_declarations

REPO: Final = Path(__file__).resolve().parents[2]
HELPER: Final = "automation/deploy_cron.sh"
JOB_ID: Final = "0123456789ab"
HELPER_SOURCE: Final = 'source "$repo_root/automation/deploy_cron.sh"'


def declared_cron(owner: str) -> Declaration:
    [row] = [row for row in all_declarations(REPO) if row.owner == owner and row.kind == "cron"]
    return row


def converge_call(row: Declaration) -> str:
    return (f'converge_cron {row.destination} "{row.attr("schedule")}" '
            f'{row.attr("script")} {row.attr("deliver")}')


def create_args(row: Declaration) -> list[str]:
    return ["cron", "create", row.attr("schedule"), "--name", row.destination, "--no-agent",
            "--script", row.attr("script"), "--deliver", row.attr("deliver")]


def listing(row: Declaration) -> str:
    return ("\n┌─────────────────────────────────────────────────────────────────────────┐\n"
            "│                         Scheduled Jobs                                  │\n"
            "└─────────────────────────────────────────────────────────────────────────┘\n\n"
            f"  {JOB_ID} [active]\n    Name:      {row.destination}\n"
            f"    Schedule:  {row.attr('schedule')}\n    Repeat:    ∞\n"
            f"    Next run:  2026-10-02T09:00:00+09:00\n    Deliver:   {row.attr('deliver')}\n"
            f"    Script:    {row.attr('script')}\n"
            "    Mode:      no-agent (script stdout delivered directly)\n\n")


def listing_only_hermes(row: Declaration, prelude: str = "") -> str:
    """Lists exactly the declared job; any other call exits 9, so a create or edit fails the deploy."""
    return ("#!/usr/bin/env bash\n" + prelude + '[[ "$*" == "cron list --all" ]] || exit 9\n'
            f"printf %s {shlex.quote(listing(row))}\n")
