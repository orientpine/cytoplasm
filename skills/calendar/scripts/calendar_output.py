from __future__ import annotations

import argparse

import calendar_core
import calendar_gate


def print_draft(record: dict[str, str | list[str]]) -> None:
    print(
        calendar_core.render_change_summary(
            action=str(record["action"]),
            summary=str(record["summary"]),
            start=str(record["start"]),
            end=str(record["end"]),
            calendar_id=str(record["calendar_id"]),
            event_id=str(record["event_id"]),
        )
    )
    print(f"DRAFT-CREATED id={record['id']} action={record['action']} sha256={record['sha256']}")
    print(
        f"실행하려면 DM으로 `실행 {record['id']}` 라고 답장하세요. "
        f"취소는 `취소 {record['id']}`."
    )


def cmd_list_drafts(_args: argparse.Namespace) -> int:
    for record in calendar_gate.list_drafts():
        print(
            f"DRAFT id={record['id']} status={record['status']} action={record['action']} "
            f"created={record['created']}"
        )
    return 0
