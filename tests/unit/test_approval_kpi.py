"""Synthetic-ledger tests for the read-only approval KPI aggregator.

Every ledger below is written by the test itself under ``tmp_path``: the module under
test never touches a production path, and the numbers asserted here are the ones a
reader can recompute by hand from the fixture.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from automation.approval_kpi.aggregate import aggregate
from automation.approval_kpi.model import ApprovalEvent, KindStats
from automation.approval_kpi.policy_table import POLICY_TABLE, PolicyEntry
from automation.approval_kpi.readers import read_root, read_skill_gate_log

DISCORD_EPOCH_MS = 1_420_070_400_000
BASE = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def snowflake(moment: datetime) -> str:
    """The inverse of the repo's own snowflake→time restoration (supply_chain_remind.py:91)."""
    return str((int(moment.timestamp() * 1000) - DISCORD_EPOCH_MS) << 22)


def skill_gate_line(
    *,
    action: str,
    target_id: str,
    requested: datetime,
    latency_seconds: int,
    method: str = "manual_reaction",
    status: str = "approved",
) -> str:
    record = {
        "action": action,
        "approval": {
            "channel": "approvals",
            "message_id": snowflake(requested),
            "method": method,
        },
        "hash": "sha256:" + "0" * 64,
        "result": {"status": status},
        "target_id": target_id,
        "timestamp": (requested + timedelta(seconds=latency_seconds))
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return json.dumps(record, sort_keys=True)


def write_log(root: Path, lines: list[str]) -> Path:
    path = root / "logs" / "approvals.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def stats_by_kind(rows: tuple[KindStats, ...]) -> dict[str, KindStats]:
    return {row.kind: row for row in rows}


def test_two_kinds_yield_hand_computable_p50_and_p95(tmp_path: Path) -> None:
    # Given: ten skill-deploy waits and two external-effect waits, all on one day.
    deploy_latencies = [60, 120, 180, 240, 300, 600, 900, 1200, 1800, 3600]
    lines = [
        skill_gate_line(
            action="skill.deploy",
            target_id=f"skill:wiki-{index}",
            requested=BASE + timedelta(minutes=index),
            latency_seconds=latency,
        )
        for index, latency in enumerate(deploy_latencies)
    ]
    lines += [
        skill_gate_line(
            action="external_effect.approval",
            target_id=f"mail:{index}",
            requested=BASE + timedelta(minutes=index),
            latency_seconds=latency,
        )
        for index, latency in enumerate((30, 90))
    ]
    _ = write_log(tmp_path, lines)

    # When: the root is aggregated.
    rows = stats_by_kind(aggregate(read_root(tmp_path)))

    # Then: nearest-rank percentiles over the known waits, and a one-day span.
    assert rows["skill-deploy"].count == 10
    assert rows["skill-deploy"].p50_seconds == 300.0
    assert rows["skill-deploy"].p95_seconds == 3600.0
    assert rows["skill-deploy"].per_day == 10.0
    assert rows["external-effect"].count == 2
    assert rows["external-effect"].p50_seconds == 30.0
    assert rows["external-effect"].p95_seconds == 90.0


def test_manual_reaction_and_decision_survive_the_read(tmp_path: Path) -> None:
    # Given: one owner reaction and one cancelled decision.
    _ = write_log(
        tmp_path,
        [
            skill_gate_line(
                action="skill.deploy",
                target_id="skill:wiki",
                requested=BASE,
                latency_seconds=45,
            ),
            skill_gate_line(
                action="skill.deploy",
                target_id="skill:atlas",
                requested=BASE,
                latency_seconds=45,
                method="reaction_absent",
                status="cancelled",
            ),
        ],
    )

    # When: the log is read directly.
    events = list(read_skill_gate_log(tmp_path / "logs" / "approvals.jsonl"))

    # Then: both records keep their decision and reaction provenance.
    assert [event.manual_reaction for event in events] == [True, False]
    assert [event.decision for event in events] == ["approved", "cancelled"]
    assert events[0].surface == "approvals"
    assert events[0].decided_at is not None
    assert (events[0].decided_at - events[0].created_at).total_seconds() == 45.0


def test_missing_root_and_empty_root_produce_no_events(tmp_path: Path) -> None:
    # Given: a root that does not exist and an empty one.
    empty = tmp_path / "empty"
    empty.mkdir()

    # When/Then: neither yields an event, and aggregation of nothing is nothing.
    assert list(read_root(tmp_path / "nonexistent")) == []
    assert list(read_root(empty)) == []
    assert aggregate(()) == ()


def test_malformed_and_injected_records_are_skipped_and_counted(tmp_path: Path) -> None:
    # Given: one good record plus one unparsable, one unknown action, one e2e injection.
    path = write_log(
        tmp_path,
        [
            skill_gate_line(
                action="skill.deploy",
                target_id="skill:wiki",
                requested=BASE,
                latency_seconds=60,
            ),
            "{not json at all",
            json.dumps({"action": "coffee.brew", "timestamp": "2026-03-01T09:00:00Z"}),
            skill_gate_line(
                action="skill.deploy",
                target_id="skill:atlas",
                requested=BASE,
                latency_seconds=60,
                method="signed_injection_e2e",
            ),
        ],
    )
    skips: dict[str, int] = {}

    # When: the log is read with a skip counter.
    events = list(read_skill_gate_log(path, skips))

    # Then: only the good record survives and each rejection names its reason.
    assert len(events) == 1
    assert skips == {"malformed": 1, "unknown-action": 1, "e2e-injected": 1}


def test_posting_journal_reservations_are_read_as_undecided_events(tmp_path: Path) -> None:
    # Given: two PostingJournal reservations, one under an unrecognizable key.
    journal = tmp_path / "gate" / "posting"
    journal.mkdir(parents=True)
    _ = (journal / "todo%3aone.posting.json").write_text(
        json.dumps({"action_hash": "a" * 64, "at": "2026-03-01T09:00:00Z", "key": "todo:one"}),
        encoding="utf-8",
    )
    _ = (journal / "mystery.posting.json").write_text(
        json.dumps({"action_hash": "b" * 64, "at": "2026-03-01T09:00:00Z", "key": "mystery"}),
        encoding="utf-8",
    )

    # When: the root is read.
    events = list(read_root(tmp_path))

    # Then: only the key whose kind is certain is kept, and it has no decision time.
    assert [(event.kind, event.decided_at) for event in events] == [("todo", None)]
    assert aggregate(events)[0].p50_seconds is None


def test_repeated_request_key_drives_the_re_request_rate() -> None:
    # Given: four events for one kind, two of which share a request key.
    def event(key: str, minute: int) -> ApprovalEvent:
        return ApprovalEvent(
            kind="todo",
            surface="approvals",
            created_at=BASE + timedelta(minutes=minute),
            decided_at=None,
            decision="pending",
            manual_reaction=False,
            request_key=key,
        )

    events = [event("todo:a", 0), event("todo:a", 1), event("todo:b", 2), event("todo:c", 3)]

    # When/Then: the two colliding events are half of the total.
    assert aggregate(events)[0].rerequest_rate == 0.5


def test_policy_table_never_guesses() -> None:
    # Given/When: the static kind policy table.
    entries = POLICY_TABLE

    # Then: every row is a frozen entry that cites a source, and unknowns say so.
    assert entries
    assert all(isinstance(entry, PolicyEntry) for entry in entries)
    for entry in entries:
        assert entry.ttl_source and entry.reminder_source
        if entry.ttl_seconds is None:
            assert entry.ttl_source.startswith("UNKNOWN:")
        else:
            assert ":" in entry.ttl_source and entry.ttl_seconds > 0
        if entry.reminder is None:
            assert entry.reminder_source.startswith("UNKNOWN:")
    assert len({entry.kind for entry in entries}) == len(entries)


def test_cli_on_a_missing_root_prints_no_records(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Given: the CLI entry point and a root that does not exist.
    from automation.approval_kpi.__main__ import main

    # When: it runs.
    code = main(["--root", str(tmp_path / "nonexistent")])

    # Then: it exits 0 with the documented empty-ledger line.
    assert code == 0
    assert "no records" in capsys.readouterr().out


def test_cli_renders_a_markdown_table(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Given: one readable record under the root.
    _ = write_log(
        tmp_path,
        [
            skill_gate_line(
                action="skill.deploy",
                target_id="skill:wiki",
                requested=BASE,
                latency_seconds=120,
            )
        ],
    )
    from automation.approval_kpi.__main__ import main

    # When: the CLI runs over that root.
    code = main(["--root", str(tmp_path)])
    out = capsys.readouterr().out

    # Then: a markdown table carries the kind and its p50.
    assert code == 0
    assert "| kind |" in out
    assert "skill-deploy" in out
    assert "120" in out
