"""A completed release leaves pending without losing its approval audit record."""
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from automation.interop.approval_lifecycle import Probe
from automation.release_retire import retire_released_record
from automation.release_spec import ReleaseSpecError


_HEAD = "a" * 40


def _pending(tmp_path: Path) -> Path:
    path = tmp_path / "pending" / "release.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps({"head_sha": _HEAD, "message_id": "123"}, sort_keys=True),
        encoding="utf-8",
    )
    return path


def test_approved_tagged_release_moves_byte_exact_into_history(tmp_path: Path) -> None:
    pending = _pending(tmp_path)
    before = pending.read_bytes()

    archived = retire_released_record(
        pending,
        tmp_path / "release-history",
        expected_head=_HEAD,
        decision=Probe.APPROVED,
    )

    assert archived == tmp_path / "release-history" / f"{_HEAD}.json"
    assert not pending.exists()
    assert archived.read_bytes() == before
    assert stat.S_IMODE(archived.stat().st_mode) == 0o600


@pytest.mark.parametrize("decision", (Probe.BOUND_PENDING, Probe.CANCELLED, Probe.UNVERIFIABLE))
def test_nonapproved_release_is_never_retired(
    tmp_path: Path,
    decision: Probe,
) -> None:
    pending = _pending(tmp_path)

    with pytest.raises(ReleaseSpecError):
        retire_released_record(
            pending,
            tmp_path / "release-history",
            expected_head=_HEAD,
            decision=decision,
        )

    assert pending.exists()


def test_different_signed_head_never_retires_the_pending_release(tmp_path: Path) -> None:
    pending = _pending(tmp_path)

    with pytest.raises(ReleaseSpecError):
        retire_released_record(
            pending,
            tmp_path / "release-history",
            expected_head="b" * 40,
            decision=Probe.APPROVED,
        )

    assert pending.exists()


def test_retire_passes_an_undecided_live_request_to_the_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """재실행=재개: 미결정 요청은 retire 가 막지 않고 lifecycle 재사용에 맡긴다.

    2026-08-31 실측: 게시만 되고 ✅ 전인 v1.0.139 요청을 재개하려던 release.sh 가
    retire 의 '미승인=BLOCK' 판정에 걸려 exit 4 로 죽었다. retire 는 실행 완료된
    승인만 archive 하고, 그 외에는 원본을 보존한 채 통과해야 한다.
    """
    from types import SimpleNamespace

    from automation import release_approval, skill_gate

    record = {
        "version": "v1.2.3",
        "head_sha": "a" * 40,
        "release_nonce": "b" * 32,
        "surface_digests": json.dumps([["skill:demo", "c" * 64]]),
        "patch_notes": "- demo change",
        "message_id": "123",
    }
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()
    pending = pending_dir / "release.json"
    pending.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)

    class _PendingGate:
        def outstanding(self, key: str) -> tuple[str, ...]:
            return ("live-request",)

        def probe(self, request: str) -> Probe:
            return Probe.BOUND_PENDING

    monkeypatch.setattr(release_approval, "_gate", lambda spec: _PendingGate())

    rc = release_approval.cmd_retire(SimpleNamespace(head="f" * 40))

    assert rc == 0
    assert pending.exists()


def test_release_calls_retire_before_planning_a_new_request() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "automation" / "release.sh"
    ).read_text(encoding="utf-8")
    retire = source.index('"${approval[@]}" retire --head "$base"')
    plan = source.index('"${plan_approval[@]}" plan --repo "$REPO_ROOT"')

    assert retire < plan
