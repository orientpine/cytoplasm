"""⛔ 된 release 승인 레코드를 감사와 함께 놓아주는 탈출구 — 3필드 일치·fsync 감사·메시지 비삭제.

새 파일인 이유: ``tests/unit/test_release_rollover.py`` 는 **승인·실행된** 릴리스만
archive 하는 retire 경로를 고정한다. abandon 은 그 반대편(소유자가 ⛔ 한 뒤 남은 레코드가
다음 요청을 막는 가용성 결함)의 운영자 탈출구이므로, 기존 파일의 재현 증적을 건드리지 않고
여기에서만 고정한다.

계약(``automation/skill_gate_retire.abandon`` 과 같은 모양):
A1  version·head_sha·message_id 세 필드가 저장된 레코드와 정확히 일치할 때만 움직인다.
A2  감사 줄을 fsync 한 뒤에야 레코드가 pending 을 떠난다.
A3  Discord 메시지는 건드리지 않는다 — 소유자의 ⛔ 는 계속 보인다.
A4  레코드는 삭제가 아니라 0600 archive 로 바이트 그대로 옮겨진다.
"""
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from automation import release_abandon, skill_gate
from automation.release_abandon import (
    ABANDON_REFUSAL_EXIT,
    ReleaseAbandon,
    ReleaseAbandonOrder,
)

_VERSION = "v1.2.3"
_HEAD = "a" * 40
_MESSAGE_ID = "1408000000000000001"
_ACTOR = "cha"
_REASON = "owner cancelled v1.2.3; the next release must be requestable"


def _record() -> dict[str, str]:
    return {
        "action_hash": "d" * 64,
        "channel_id": "999",
        "head_sha": _HEAD,
        "kind": "release",
        "message_id": _MESSAGE_ID,
        "patch_notes": "- demo change",
        "policy_version": "1",
        "release_nonce": "b" * 32,
        "render_version": "2",
        "surface_digests": json.dumps([["skill:demo", "c" * 64]], separators=(",", ":")),
        "surface": "owner-dm",
        "version": _VERSION,
    }


def _pending(gate_dir: Path) -> Path:
    path = gate_dir / "pending" / "release.json"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(_record(), sort_keys=True), encoding="utf-8")
    path.chmod(0o600)
    return path


def _order(**overrides: str) -> ReleaseAbandonOrder:
    fields = {
        "version": _VERSION,
        "head_sha": _HEAD,
        "message_id": _MESSAGE_ID,
        "reason": _REASON,
        "actor": _ACTOR,
    }
    fields.update(overrides)
    return ReleaseAbandonOrder(**fields)


def _audit(gate_dir: Path) -> Path:
    return gate_dir / "logs" / "approval-abandons.jsonl"


def _audit_lines(gate_dir: Path) -> list[dict[str, str]]:
    path = _audit(gate_dir)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _archived(gate_dir: Path) -> list[Path]:
    root = gate_dir / release_abandon.ABANDON_ARCHIVE_DIRNAME
    return sorted(root.iterdir()) if root.exists() else []


@pytest.mark.parametrize(
    "overrides",
    (
        {"version": "v9.9.9"},
        {"head_sha": "e" * 40},
        {"message_id": "1408000000000000999"},
    ),
)
def test_an_identity_field_that_does_not_match_refuses_and_keeps_the_record(
    tmp_path: Path,
    overrides: dict[str, str],
) -> None:
    pending = _pending(tmp_path)
    before = pending.read_bytes()

    result = release_abandon.abandon(tmp_path, _order(**overrides), _audit(tmp_path))

    assert result.outcome is ReleaseAbandon.IDENTITY_MISMATCH
    assert result.exit_code == ABANDON_REFUSAL_EXIT
    assert pending.read_bytes() == before
    assert _archived(tmp_path) == []
    assert _audit_lines(tmp_path) == []


def test_no_pending_release_record_refuses_as_absent(tmp_path: Path) -> None:
    result = release_abandon.abandon(tmp_path, _order(), _audit(tmp_path))

    assert result.outcome is ReleaseAbandon.RECORD_ABSENT
    assert result.exit_code == ABANDON_REFUSAL_EXIT
    assert _audit_lines(tmp_path) == []


def test_every_field_matching_archives_the_record_byte_exact_and_audits_the_override(
    tmp_path: Path,
) -> None:
    pending = _pending(tmp_path)
    before = pending.read_bytes()

    result = release_abandon.abandon(tmp_path, _order(), _audit(tmp_path))

    assert result.outcome is ReleaseAbandon.ABANDONED
    assert result.exit_code == 0
    assert not pending.exists()
    archived = _archived(tmp_path)
    assert [path.name.startswith(f"{_HEAD}.") for path in archived] == [True]
    assert archived[0].read_bytes() == before
    assert stat.S_IMODE(archived[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(archived[0].parent.stat().st_mode) == 0o700
    assert result.archived == archived[0]
    audited = _audit_lines(tmp_path)
    assert len(audited) == 1
    assert audited[0]["event"] == "release-abandon"
    assert audited[0]["actor"] == _ACTOR
    assert audited[0]["reason"] == _REASON
    assert audited[0]["version"] == _VERSION
    assert audited[0]["head_sha"] == _HEAD
    assert audited[0]["message_id"] == _MESSAGE_ID
    assert audited[0]["action_hash"] == _record()["action_hash"]
    assert audited[0]["archive"] == archived[0].name
    assert audited[0]["timestamp"]


def test_an_unwritable_audit_trail_leaves_the_record_in_place(tmp_path: Path) -> None:
    pending = _pending(tmp_path)
    before = pending.read_bytes()
    (tmp_path / "logs").write_text("not a directory", encoding="utf-8")

    result = release_abandon.abandon(tmp_path, _order(), _audit(tmp_path))

    assert result.outcome is ReleaseAbandon.AUDIT_FAILED
    assert result.exit_code == ABANDON_REFUSAL_EXIT
    assert pending.read_bytes() == before
    assert _archived(tmp_path) == []


def test_the_cli_abandons_without_a_single_discord_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pending = _pending(tmp_path)

    def _forbidden(*arguments: object, **keywords: object) -> object:
        raise AssertionError("abandon must never talk to Discord")

    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", tmp_path / "logs" / "approvals.jsonl")
    monkeypatch.setattr(skill_gate, "_api", _forbidden)
    monkeypatch.setenv("SUDO_USER", _ACTOR)

    exit_code = release_abandon.main(
        [
            "--version",
            _VERSION,
            "--head",
            _HEAD,
            "--message-id",
            _MESSAGE_ID,
            "--reason",
            _REASON,
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "RELEASE-ABANDONED" in captured.out
    assert _MESSAGE_ID not in captured.out  # 스노플레이크는 마스킹되어 로그로 새지 않는다
    assert not pending.exists()
    assert [line["actor"] for line in _audit_lines(tmp_path)] == [_ACTOR]


def test_the_cli_refuses_a_mismatched_identity_on_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pending = _pending(tmp_path)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", tmp_path / "logs" / "approvals.jsonl")

    exit_code = release_abandon.main(
        [
            "--version",
            _VERSION,
            "--head",
            "e" * 40,
            "--message-id",
            _MESSAGE_ID,
            "--reason",
            _REASON,
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == ABANDON_REFUSAL_EXIT
    assert "RELEASE-ABANDON-REFUSED" in captured.err
    assert f"reason={ReleaseAbandon.IDENTITY_MISMATCH.value}" in captured.err
    assert pending.exists()
