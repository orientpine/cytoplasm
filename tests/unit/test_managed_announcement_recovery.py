from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation.interop.approval_lease import PostingJournal
from automation.managed_skills.announce import AnnounceOutcome, announce_release
from automation.managed_skills.announce_ledger import (
    AnnounceLedger,
    announce_action_hash,
    announce_key,
)
from automation.managed_skills.announcement_recovery import (
    AnnouncementRecoveryError,
    RecoveryRequest,
    abandon_announcement,
    main,
)
from automation.managed_skills.manifest import ManagedManifest, manifest_digest

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
        assert json.loads(
            audit_path.read_text(encoding="utf-8").splitlines()[-1]
        ) == json.loads(expected)
        original_clear(self, key)

    monkeypatch.setattr(PostingJournal, "clear", assert_audited_before_clear)

    # When: the operator invokes the repository recovery path after deciding not-delivered.
    _ = abandon_announcement(
        RecoveryRequest(tmp_path, _KEY, _ACTION_HASH, "cha", "confirmed-not-delivered")
    )

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
        _ = abandon_announcement(
            RecoveryRequest(tmp_path, key, action_hash, "cha", "confirmed-not-delivered")
        )
    assert journal.outstanding(_KEY) is not None
    assert not (tmp_path / "announcement-abandon.audit.jsonl").exists()


def test_abandon_announcement_cli_when_reservation_exists_then_audits_and_unblocks_publish(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: an ambiguous posting reservation for a release the operator confirmed was not sent.
    manifest = ManagedManifest(
        schema_version=1,
        publisher="cha",
        skill="managed-x",
        release_sequence=7,
        source_commit=None,
        skill_sha256="a" * 64,
        previous_sha256=None,
        compatibility="any",
        breaking=False,
        revoked_digests=(),
        changelog="Recovery test.",
        migration=None,
    )
    tag = "managed-x/v7"
    key = announce_key(manifest.skill, tag)
    action_hash = announce_action_hash(
        manifest_digest=manifest_digest(manifest), tag=tag, channel_id="123"
    )
    _ = PostingJournal(tmp_path).reserve(key, action_hash, "2026-08-21T00:00:00Z")

    # When: the explicit audited CLI escape hatch abandons that exact reservation.
    assert main([
        "abandon", "--key", key, "--action-hash", action_hash,
        "--actor", "cha", "--reason", "confirmed-not-delivered",
        "--state-dir", str(tmp_path),
    ]) == 0

    # Then: a later publish can post once, while the audit identifies who did what and why.
    class Sent:
        @property
        def message_id(self) -> str:
            return "m1"

    class Transport:
        def send(self, body: str) -> tuple[Sent, ...]:
            _ = body
            return (Sent(),)

    result = announce_release(
        manifest, tag, transport=Transport(), channel_id="123", ledger=AnnounceLedger(tmp_path)
    )
    assert result.outcome is AnnounceOutcome.POSTED
    audit = (tmp_path / "announcement-abandon.audit.jsonl").read_text(encoding="utf-8")
    assert json.loads(audit.splitlines()[0]) == {
        "action_hash": action_hash,
        "actor": "cha",
        "event": "announcement-abandon-requested",
        "key": key,
        "reason": "confirmed-not-delivered",
    }
    assert "ANNOUNCEMENT-ABANDONED" in capsys.readouterr().out


def test_abandon_announcement_cli_when_reservation_is_absent_then_refuses_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: no reservation exists for the release named by the operator.
    key = "managed-announce:managed-x:managed-x/v7"

    # When/Then: recovery refuses and produces no audit record that could imply success.
    assert main([
        "abandon", "--key", key, "--action-hash", _ACTION_HASH,
        "--actor", "cha", "--reason", "confirmed-not-delivered",
        "--state-dir", str(tmp_path),
    ]) == 1
    assert "reservation is absent" in capsys.readouterr().err
    assert not (tmp_path / "announcement-abandon.audit.jsonl").exists()


def test_abandon_announcement_when_lease_is_held_refuses_without_clearing(tmp_path: Path) -> None:
    # Given: the announcement producer still owns the same logical key lease.
    journal = _reserve(tmp_path)
    from automation.managed_skills.announce_ledger import AnnounceLedger

    with AnnounceLedger(tmp_path).lease.hold(_KEY) as owned:
        assert owned

        # When/Then: recovery cannot race the producer or mutate its reservation.
        with pytest.raises(AnnouncementRecoveryError):
            _ = abandon_announcement(
                RecoveryRequest(tmp_path, _KEY, _ACTION_HASH, "cha", "confirmed-not-delivered")
            )
    assert journal.outstanding(_KEY) is not None
