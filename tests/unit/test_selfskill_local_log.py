"""Local self-skill audit records survive a notification-only daily watch.

The owner DM is deliberately ephemeral.  These tests pin the node-local record that
lets a later operator inspect no-change runs and decide each detected overlap.
"""
from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path

from automation.selfskill_audit import local_log
from automation.selfskill_audit.delta import Action, Delta
from automation.selfskill_audit.overlap import OverlapHit

_NOW = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
_LATER = datetime(2026, 9, 3, 9, 0, tzinfo=UTC)


def test_append_run_writes_even_a_no_change_audit_with_private_permissions(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATE_ROOT", str(tmp_path / "state"))
    delta = Delta(Action.CREATED, "agent-notes", "a" * 64, "agent", False, None, "2026-09-02T09:00:00Z")
    hit = OverlapHit("meeting-notes", "meeting", 0.62, ("agenda", "minutes"))

    local_log.append_run(
        now=_NOW,
        account="agent",
        deltas=(delta,),
        shadowed=("mail",),
        overlaps=(hit,),
        notified=True,
    )
    local_log.append_run(
        now=_NOW,
        account="agent",
        deltas=(),
        shadowed=(),
        overlaps=(),
        notified=False,
    )

    path = tmp_path / "state" / "logs" / "selfskill-audit" / "2026-09.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows == [
        {
            "account": "agent",
            "delta_counts": {"archived": 0, "created": 1, "edited": 0, "removed": 0, "restored": 0},
            "notified": True,
            "overlaps": [{"governed": "meeting", "score": 0.62, "self": "meeting-notes"}],
            "shadowed": ["mail"],
            "ts": "2026-09-02T09:00:00Z",
        },
        {
            "account": "agent",
            "delta_counts": {"archived": 0, "created": 0, "edited": 0, "removed": 0, "restored": 0},
            "notified": False,
            "overlaps": [],
            "shadowed": [],
            "ts": "2026-09-02T09:00:00Z",
        },
    ]
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_append_run_when_storage_fails_reports_once_without_raising(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(local_log, "_state_root", lambda home: tmp_path / "not-a-directory")
    _ = (tmp_path / "not-a-directory").write_text("blocked", encoding="utf-8")

    local_log.append_run(
        now=_NOW,
        account="agent",
        deltas=(),
        shadowed=(),
        overlaps=(),
        notified=False,
    )

    assert capsys.readouterr().err.startswith("LOCAL-LOG-FAIL ")


def test_update_pending_overlaps_refreshes_current_hits_and_removes_resolved_ones(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATE_ROOT", str(tmp_path / "state"))
    initial = OverlapHit("meeting-notes", "meeting", 0.62, ("agenda", "minutes"))
    refreshed = OverlapHit("meeting-notes", "meeting", 0.8, ("agenda", "minutes", "owner"))

    assert local_log.update_pending_overlaps(now=_NOW, overlaps=(initial,)) == 1
    assert local_log.update_pending_overlaps(now=_LATER, overlaps=(refreshed,)) == 1
    assert local_log.update_pending_overlaps(now=_LATER, overlaps=()) == 0

    path = tmp_path / "state" / "selfskill-audit" / "pending-overlaps.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {}
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_pending_overlap_first_seen_is_preserved_when_the_hit_is_refreshed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATE_ROOT", str(tmp_path / "state"))
    initial = OverlapHit("meeting-notes", "meeting", 0.62, ("agenda", "minutes"))
    refreshed = OverlapHit("meeting-notes", "meeting", 0.8, ("agenda", "minutes", "owner"))

    _ = local_log.update_pending_overlaps(now=_NOW, overlaps=(initial,))
    _ = local_log.update_pending_overlaps(now=_LATER, overlaps=(refreshed,))

    path = tmp_path / "state" / "selfskill-audit" / "pending-overlaps.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "meeting-notes→meeting": {
            "first_seen": "2026-09-02T09:00:00Z",
            "last_seen": "2026-09-03T09:00:00Z",
            "score": 0.8,
            "shared": ["agenda", "minutes", "owner"],
        }
    }
