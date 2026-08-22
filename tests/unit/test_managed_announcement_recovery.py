from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation.interop.approval_lease import PostingJournal
from automation.managed_skills.announcement_recovery import (
    AnnouncementRecoveryError,
    RecoveryRequest,
    abandon_announcement,
)

_KEY = "managed-announce:managed-x:managed-x/v7"
_ACTION_HASH = f"sha256:{'a' * 64}"


def _reserve(root: Path) -> PostingJournal:
    journal = PostingJournal(root)
    journal.reserve(_KEY, _ACTION_HASH, "2026-08-21T00:00:00Z")
    return journal


def test_abandon_announcement_when_binding_matches_audits_before_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a stale, hash-bound posting reservation and an observable clear boundary.
    journal = _reserve(tmp_path)
    audit_path = tmp_path / "announcement-abandon.audit.jsonl"
    original_clear = PostingJournal.clear

    def assert_audited_before_clear(self: PostingJournal, key: str) -> None:
        expected = json.dumps(
            {
                "event": "posting-abandoned",
                "key": _KEY,
                "reservation": {
                    "action_hash": _ACTION_HASH,
                    "at": "2026-08-21T00:00:00Z",
                    "key": _KEY,
                },
            },
            sort_keys=True,
        )
        assert audit_path.read_text(encoding="utf-8") == f"{expected}\n"
        original_clear(self, key)

    monkeypatch.setattr(PostingJournal, "clear", assert_audited_before_clear)

    # When: the operator invokes the repository recovery path after deciding not-delivered.
    _ = abandon_announcement(RecoveryRequest(tmp_path, _KEY, _ACTION_HASH))

    # Then: the reservation is clear only after its bound audit event is durable.
    assert journal.outstanding(_KEY) is None


@pytest.mark.parametrize(
    ("key", "action_hash"),
    (("managed-announce:managed-x:managed-x/v8", _ACTION_HASH), (_KEY, f"sha256:{'b' * 64}")),
)
def test_abandon_announcement_when_binding_is_wrong_refuses_without_clearing(
    tmp_path: Path, key: str, action_hash: str
) -> None:
    # Given: one reservation and a recovery request with the wrong key or hash.
    journal = _reserve(tmp_path)

    # When/Then: recovery fails closed and preserves both the journal and absent audit.
    with pytest.raises(AnnouncementRecoveryError):
        _ = abandon_announcement(RecoveryRequest(tmp_path, key, action_hash))
    assert journal.outstanding(_KEY) is not None
    assert not (tmp_path / "announcement-abandon.audit.jsonl").exists()


def test_abandon_announcement_when_lease_is_held_refuses_without_clearing(tmp_path: Path) -> None:
    # Given: the announcement producer still owns the same logical key lease.
    journal = _reserve(tmp_path)
    from automation.managed_skills.announce_ledger import AnnounceLedger

    with AnnounceLedger(tmp_path).lease.hold(_KEY) as owned:
        assert owned

        # When/Then: recovery cannot race the producer or mutate its reservation.
        with pytest.raises(AnnouncementRecoveryError):
            _ = abandon_announcement(RecoveryRequest(tmp_path, _KEY, _ACTION_HASH))
    assert journal.outstanding(_KEY) is not None
