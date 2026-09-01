"""VA-2 wiring in deploy-skill.sh: one release ✅ replaces per-skill ✅ — and nothing else.

Same source-structure style as ``test_deploy_skill_denial_branch.py``: the script is a
1000-line privileged pipeline, and what must hold is WHERE the release path plugs in —
sandbox/review/peer attestation stay, only the owner-approval cycle is swapped, the
mount recheck goes through the same release gate, and no per-skill record is retired.
"""
from __future__ import annotations

from pathlib import Path

_DEPLOY = Path(__file__).resolve().parents[2] / "automation" / "deploy-skill.sh"
_SOURCE = _DEPLOY.read_text(encoding="utf-8")


def _index(needle: str) -> int:
    position = _SOURCE.find(needle)
    assert position != -1, f"missing from deploy-skill.sh: {needle!r}"
    return position


def _release_branch() -> str:
    start = _index('if [[ "$RELEASE_APPROVAL" == 1 ]]; then\n  # VA-2')
    end = _SOURCE.find('elif [[ -n "${APPROVAL_MESSAGE_ID:-}" ]]; then', start)
    assert end != -1, "release branch must fall back to the existing approval paths"
    return _SOURCE[start:end]


def test_the_flag_exists_and_defaults_off() -> None:
    assert "--release-approval) RELEASE_APPROVAL=1 ;;" in _SOURCE
    assert "RELEASE_APPROVAL=0" in _SOURCE


def test_the_release_branch_never_posts_a_per_skill_request() -> None:
    """요청을 게시하면 아무도 ✅ 하지 않을 레코드가 남아 리마인더가 매시간 운다."""
    branch = _release_branch()
    assert "release-authorize --skill" in branch
    assert "request --skill" not in branch


def test_a_cancelled_release_maps_to_the_retire_exit() -> None:
    branch = _release_branch()
    assert "exit 9" in branch, "⛔ 는 재시도가 아니라 회수 신호(exit 9)여야 한다"


def test_an_uncovered_digest_fails_closed_with_its_own_block() -> None:
    branch = _release_branch()
    assert "RELEASE-APPROVAL-BLOCK" in branch
    assert "옆 승인으로 못 탄다" in branch


def test_the_mount_recheck_uses_the_release_gate_too() -> None:
    """마운트 직전 재검증이 per-skill check 로 새면 릴리스 해시 바인딩이 풀린다."""
    assert 'release_authorize_with_refresh "$DIGEST"' in _SOURCE
    assert 'release_authorize_with_refresh "$CURRENT_DIGEST"' in _SOURCE


def test_peer_attestation_survives_including_the_refresh_path() -> None:
    start = _index("release_authorize_with_refresh() {")
    end = _SOURCE.find("\n}", start)
    body = _SOURCE[start:end]
    assert "peer_attest " in body
    assert "--refresh" in body, "만료된 peer 증명은 갱신 1회 후 재판정한다(기존 계약 미러)"


def test_no_per_skill_record_is_retired_under_the_release_path() -> None:
    guard = _index("no per-skill pending record to retire")
    consume = _index('gate "" consume --skill')
    assert guard < consume, "consume 은 release 분기의 else 아래에만 있어야 한다"


def test_release_approval_rejects_personal_and_managed_deploys() -> None:
    assert "applies to repository skill deploys only" in _release_branch()


def test_release_approval_has_no_injected_approval_variant() -> None:
    assert "no injected-approval variant" in _release_branch()
