"""수렴 helper 가 스냅샷을 실제 아카이브로 만들어내는지.

2026-08-02 실측: 앞선 두 결함(락 위치·ProtectHome)을 고치자 tick 이 드디어 converge 까지
도달했고, 거기서 세 번째 결함이 나왔다.

    bash: line 4: AUTOPHAGY_SNAPSHOT_DIR: unbound variable
    RELEASE-STORE-BLOCK: archive extraction failed: empty file

helper 는 `origin_snapshot_run <mirror> <sha> tar -C "$AUTOPHAGY_SNAPSHOT_DIR" ...` 로
불렀는데, 그 변수는 `origin_snapshot_run` 이 **명령을 실행할 때** 설정한다. 인자 목록을
만드는 시점에는 아직 없으므로 `set -u` 아래에서 호출 셸이 죽고, 파이프에는 빈 바이트가
흘러 릴리스 저장소가 "empty file" 로 거부했다.

ops 쪽 converger 는 tar 를 자기 `bash -c` 로 감싸 그 시점을 미뤄 이 함정을 피했다.
helper 는 대신 문서화된 계약(명령은 cwd = 스냅샷 트리에서 돈다)에 기대 `-C` 를 버렸다.

**소스 문자열만 검사하면 이 결함을 못 잡는다** — 인용부호가 맞는지가 아니라 결과가 빈
아카이브인지가 문제이기 때문이다. 그래서 실제로 돌려 아카이브 내용을 확인한다.
"""
from __future__ import annotations

import subprocess
import tarfile
from io import BytesIO
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SNAPSHOT = _REPO / "automation" / "origin_snapshot.sh"
_HELPER = _REPO / "automation" / "converge_origin_main.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args), check=True, capture_output=True, text=True
    ).stdout.strip()


def _mirror(tmp_path: Path) -> tuple[Path, str]:
    """origin + 그것을 추적하는 미러 체크아웃, 그리고 대상 sha."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _ = subprocess.run(("git", "init", "--bare", "-b", "main", str(origin)), check=True, capture_output=True)
    checkout = tmp_path / "mirror"
    checkout.mkdir()
    _ = subprocess.run(("git", "init", "-b", "main", str(checkout)), check=True, capture_output=True)
    _git(checkout, "config", "user.email", "t@example.invalid")
    _git(checkout, "config", "user.name", "t")
    _ = (checkout / "marker.txt").write_text("payload\n", encoding="utf-8")
    (checkout / "automation").mkdir()
    _ = (checkout / "automation" / "thing.py").write_text("x = 1\n", encoding="utf-8")
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-m", "seed")
    _git(checkout, "remote", "add", "origin", str(origin))
    _git(checkout, "push", "-q", "origin", "main")
    return checkout, _git(checkout, "rev-parse", "HEAD")


def _run_snapshot_command(mirror: Path, sha: str, command: str, tmp_path: Path) -> subprocess.CompletedProcess[bytes]:
    """helper 와 같은 형태로 origin_snapshot_run 을 돌린다 (set -u 포함)."""
    script = f'set -euo pipefail\n. "{_SNAPSHOT}"\norigin_snapshot_run "{mirror}" "{sha}" {command}\n'
    return subprocess.run(
        ("bash", "-c", script), capture_output=True, check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)},
    )


def test_the_helpers_tar_form_produces_a_real_archive(tmp_path: Path) -> None:
    # Given: helper 가 쓰는 바로 그 명령 (cwd 계약에 기대고 -C 없음)
    mirror, sha = _mirror(tmp_path)
    # When
    result = _run_snapshot_command(mirror, sha, "tar --exclude=.git -czf - .", tmp_path)
    # Then: 비어 있지 않아야 하고, 내용이 실제로 들어 있어야 한다
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout, "빈 아카이브 — 릴리스 저장소가 'empty file' 로 거부한다"
    with tarfile.open(fileobj=BytesIO(result.stdout), mode="r:gz") as archive:
        names = {Path(name).as_posix().lstrip("./") for name in archive.getnames()}
    assert "marker.txt" in names
    assert "automation/thing.py" in names
    assert not any(name.startswith(".git/") for name in names), ".git 은 제외돼야 한다"


def test_expanding_the_snapshot_dir_in_the_argument_list_is_the_trap(tmp_path: Path) -> None:
    """왜 -C 를 뺐는지 고정한다 — 이 형태는 set -u 아래에서 반드시 죽는다.

    이 테스트가 실패한다면 트랩이 사라진 것이므로, 그때는 helper 를 되돌려도 된다.
    """
    mirror, sha = _mirror(tmp_path)
    result = _run_snapshot_command(
        mirror, sha, 'tar -C "$AUTOPHAGY_SNAPSHOT_DIR" --exclude=.git -czf - .', tmp_path
    )
    assert result.returncode != 0
    assert b"AUTOPHAGY_SNAPSHOT_DIR" in result.stderr
    assert not result.stdout, "빈 바이트가 흘러 저장소가 empty file 로 거부한 경로"


def test_the_helper_does_not_expand_the_snapshot_dir_when_building_arguments() -> None:
    """helper 소스가 그 함정 형태로 돌아가지 않도록 못박는다."""
    body = _HELPER.read_text(encoding="utf-8")
    invocation = next(
        line for line in body.splitlines() if line.strip().startswith("origin_snapshot_run ")
    )
    assert "AUTOPHAGY_SNAPSHOT_DIR" not in invocation, invocation
