"""무패치 구동을 조용히 넘어가지 않는가 — hermes_compat 패치 상태 프로브.

2026-08-16~18 실측: `hermes update` 가 소스를 갈아엎으면서 autostash 를 복원하지 않아
`hermes_compat` 패치 **3종이 통째로** 빠진 채 프로덕션이 이틀을 돌았다. 그중
`busy-path-pre-gateway-dispatch` 는 busy 경로 메시지가 skill-generation 관측(W6-4)과
meeting-gate fail-closed veto(W2-3) 훅을 타지 못하는 것을 메우는 패치다 — 즉 게이트 인접
훅이 꺼진 상태였다. 그리고 그 사실을 말해주는 것은 아무것도 없었다: 유닛은 내내
active/running 이었고 매니페스트의 notes 는 "healthcheck should surface a missing patch"
라고 적고 있었지만 `automation/healthcheck.sh` 에는 그런 검사가 **한 줄도 없었다**.

여기서 만드는 것은 재적용이 아니라 **탐지**다. 실제 재적용과 게이트웨이 쌍 재시동은 외부효과라
소유자 원장(todo 15) 소관이다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from automation.hermes_compat import patch_state

_REPO = Path(__file__).resolve().parents[2]
_CARRIER = _REPO / "automation" / "hermes_compat"


def _install(root: Path, *, applied: tuple[str, ...]) -> Path:
    """Build a fake gateway install whose files carry only the named markers.

    Two patches share `gateway/run.py`, so markers accumulate per target file — writing
    one file per patch would silently drop the first marker and fake a half deploy.
    """
    bodies: dict[str, str] = {}
    for patch in patch_state.load_patches():
        body = bodies.setdefault(patch.target, "# gateway source\n")
        if patch.patch_id in applied:
            bodies[patch.target] = body + f"{patch.marker} = True\n"
    for relative, body in bodies.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


def _run_cli(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "automation.hermes_compat.patch_state", "--install-root", str(root)],
        capture_output=True, text=True, check=False, timeout=60, cwd=str(_REPO),
    )


def test_a_fully_patched_install_is_quiet_and_exits_zero(tmp_path: Path) -> None:
    every = tuple(patch.patch_id for patch in patch_state.load_patches())
    _install(tmp_path, applied=every)

    result = _run_cli(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PATCHED" in result.stdout


def test_an_unpatched_install_exits_non_zero_and_names_every_missing_patch(
    tmp_path: Path,
) -> None:
    _install(tmp_path, applied=())

    result = _run_cli(tmp_path)

    # Then: the state that ran two days in production is now loud, and it names each
    # patch rather than a single opaque "drift" line.
    assert result.returncode == 1, result.stdout + result.stderr
    for patch in patch_state.load_patches():
        assert patch.patch_id in result.stdout


def test_a_half_deployed_install_is_a_failure_not_a_pass(tmp_path: Path) -> None:
    """두 deploy 스크립트 중 하나만 돌린 상태 — 실측된 실제 노드 상태다."""
    _install(tmp_path, applied=("busy-path-pre-gateway-dispatch",))

    result = _run_cli(tmp_path)

    assert result.returncode == 1
    assert "owner-dm-busy-fifo" in result.stdout
    assert "discord-per-message-receipts" in result.stdout


def test_an_absent_target_file_is_unknown_rather_than_missing(tmp_path: Path) -> None:
    # Given: an install root where the gateway source is not where we expect.
    (tmp_path / "empty").mkdir()

    result = _run_cli(tmp_path / "empty")

    # Then: 부재 != PASS, and also != "missing" — we cannot read it, so we say so.
    assert result.returncode == 2, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_an_unreadable_manifest_refuses_to_report_a_clean_bill(tmp_path: Path) -> None:
    broken = tmp_path / "manifest.json"
    broken.write_text("{ not json", encoding="utf-8")

    with pytest.raises(patch_state.PatchStateError):
        patch_state.load_patches(broken)


def test_the_manifest_no_longer_claims_a_check_that_does_not_exist() -> None:
    manifest = json.loads((_CARRIER / "manifest.json").read_text(encoding="utf-8"))
    notes = " ".join(manifest["notes"]).lower()

    # Then: the note points at something that is actually runnable. The old text sent a
    # reader to automation/healthcheck.sh, which never mentioned hermes_compat at all —
    # a false assurance is worse than no assurance during an incident.
    assert "patch_state" in notes
    healthcheck = (_REPO / "automation" / "healthcheck.sh").read_text(encoding="utf-8")
    if "hermes_compat" not in healthcheck:
        assert "healthcheck" not in notes or "아직" in notes or "not done yet" in notes


def test_every_manifest_patch_is_carried_by_some_deploy_script() -> None:
    """3종을 다 올리려면 두 스크립트를 모두 돌려야 한다 — 그 사실을 기계로 고정한다."""
    scripts = {
        path.name: path.read_text(encoding="utf-8")
        for path in _CARRIER.glob("deploy*.sh")
    }
    assert scripts, "deploy 스크립트를 찾지 못했다"

    uncarried = [
        patch.applier
        for patch in patch_state.load_patches()
        if not any(patch.applier in text for text in scripts.values())
    ]

    assert not uncarried, (
        "매니페스트에 있는데 어떤 deploy 스크립트도 싣지 않는 applier — 배포해도 그 패치는 "
        f"영원히 빠진 채로 남는다: {uncarried}"
    )
