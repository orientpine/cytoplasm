"""마운트된 릴리스 깊이에서 repo 모듈을 찾는 계약.

`test_meeting_project.py` 가 아니라 별도 파일인 이유는 검증 방식이 다르기 때문이다 —
여기서는 실제 마운트와 **같은 깊이**로 스킬 사본을 깔고 **별도 프로세스**에서 import 한다.
같은 프로세스에서는 이미 로드된 `meeting_project` 와 bare import 가 충돌해, 통과해도
아무것도 증명하지 못한다.

2026-08-28 실측(노드): 라이브 마운트 실경로는
`/srv/autophagy-skills/releases/meeting/<digest>/scripts` 라 `parents[3]` 가
`/srv/autophagy-skills/releases` 였고, 거기에 `automation` 이 없어
`_repo` 가 `ModuleNotFoundError` 로 죽었다. 그 실패는 `BOARD-FETCH-FAIL` 마커로 삼켜져
양식·action item 원장·미처리 전사본 조회가 **전부 조용히 무력**했다.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "meeting"
sys.path.insert(0, str(SKILL / "scripts"))

import meeting_runtime  # noqa: E402


def _copy_at_live_mount_depth(tmp_path: Path) -> Path:
    mounted = (
        tmp_path / "srv" / "autophagy-skills" / "releases" / "meeting" / "digest" / "scripts"
    )
    mounted.mkdir(parents=True)
    for path in (SKILL / "scripts").glob("meeting_*.py"):
        shutil.copy(path, mounted / path.name)
    return mounted


def _probe(mounted: Path, release: Path) -> subprocess.CompletedProcess[str]:
    script = f"""
import pathlib, sys
sys.path.insert(0, {str(mounted)!r})
import meeting_runtime
meeting_runtime.RELEASE_CURRENT = pathlib.Path({str(release)!r})
meeting_runtime.MIRROR_CHECKOUT = pathlib.Path({str(release / "absent")!r})
import meeting_project
module = meeting_project._repo("drive_outputs")
print("RESOLVED", pathlib.Path(module.__file__).resolve())
"""
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin", "HOME": str(mounted.parent)},
    )


def test_repo_import_survives_the_mounted_release_depth(tmp_path: Path) -> None:
    """마운트 깊이에는 repo 가 없다 — 깊이 추측이 아니라 릴리스 리졸버가 답해야 한다."""
    result = _probe(_copy_at_live_mount_depth(tmp_path), REPO)

    assert result.returncode == 0, f"마운트 깊이에서 automation 을 찾지 못했다:\n{result.stderr}"
    assert "RESOLVED" in result.stdout
    assert str(REPO / "automation" / "drive_outputs.py") in result.stdout


def test_runtime_root_falls_back_to_the_release_when_the_depth_has_no_repo() -> None:
    mounted = Path("/srv/autophagy-skills/releases/meeting/abc/scripts/meeting_cli.py")

    resolved = meeting_runtime.runtime_root(mounted, current=REPO, mirror=Path("/absent"))

    assert resolved == REPO


def test_runtime_root_reads_the_module_constants_when_no_override_is_given(monkeypatch) -> None:
    """기본값이 정의 시점에 굳으면 릴리스 경로를 운영 중에 바꿔 끼울 수 없다."""
    monkeypatch.delenv("AUTOPHAGY_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("AUTOPHAGY_REPO_ROOT", raising=False)
    monkeypatch.setattr(meeting_runtime, "RELEASE_CURRENT", REPO)
    monkeypatch.setattr(meeting_runtime, "MIRROR_CHECKOUT", Path("/absent"))

    assert meeting_runtime.runtime_root(Path("/srv/x/y/z/scripts/m.py")) == REPO


def test_explicit_override_still_wins(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTOPHAGY_RUNTIME_ROOT", str(tmp_path))

    assert meeting_runtime.runtime_root() == tmp_path
