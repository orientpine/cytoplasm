"""불변 릴리스에서 배포할 때의 provenance — 트리 전체가 커밋과 같아야 한다.

배포 가드는 "git 에 없는 코드는 배포하지 않는다"를 워킹트리 blob 대조로 지켜왔다.
그런데 DG-5 가 런타임을 `.git` 없는 읽기 전용 스냅샷으로 옮기면서, 자율 배포 경로
(⑦ 워처)가 매번 `DEPLOY-BLOCK: not a git checkout` 으로 막혔다. 2026-08-02 실측.

여기서 고정하는 것은 그 구멍을 메우되 **보장을 약화하지 않는** 방식이다.

* `.origin-sha` 는 신원 증거일 뿐 provenance 가 아니다. 설치기가 임의의 아카이브와
  임의의 SHA 를 받아 그대로 마커에 적으므로, 마커만 믿으면 "이 커밋에서 왔다"는
  주장을 검증 없이 수용하게 된다. 그래서 트리 전체를 커밋과 대조한다.
* tracked 파일만 해시하면 예전 디렉터리 버그가 재현된다 — 커밋에 없는 여분 파일은
  목록에 안 잡히면서 아카이브에는 실려 간다. 그래서 **경로 집합이 정확히 같아야**
  하며, 유일한 예외는 설치기가 만든 `.origin-sha` 하나다.
* 심링크·특수파일은 거부한다. 릴리스는 봉인된 트리이므로 그런 항목이 있을 이유가 없고,
  심링크 하나면 검증한 바이트와 실제로 열리는 바이트가 갈라진다.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from automation.release_provenance import verify_release


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(root), *args), capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _origin(tmp_path: Path) -> tuple[Path, str]:
    """커밋 하나를 담은 저장소와 그 sha — 릴리스가 대조될 진실."""
    origin = tmp_path / "origin"
    (origin / "automation").mkdir(parents=True)
    _ = (origin / "AGENTS.md").write_text("규약\n", encoding="utf-8")
    _ = (origin / "automation" / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    (origin / "automation" / "run.sh").chmod(0o755)
    _ = (origin / "automation" / "한글.md").write_text("한글 경로\n", encoding="utf-8")
    _git(origin, "init", "--quiet", "--initial-branch=main")
    _git(origin, "config", "user.email", "t@example.invalid")
    _git(origin, "config", "user.name", "t")
    _git(origin, "add", "-A")
    _git(origin, "commit", "--quiet", "-m", "seed")
    return origin, _git(origin, "rev-parse", "HEAD")


def _mirror(tmp_path: Path, origin: Path, sha: str) -> Path:
    """origin/main 을 들고 있는 읽기 전용 git 객체 저장소(노드의 미러 역할)."""
    mirror = tmp_path / "mirror"
    _git(tmp_path, "clone", "--quiet", str(origin), str(mirror))
    _git(mirror, "update-ref", "refs/remotes/origin/main", sha)
    return mirror


def _release(tmp_path: Path, origin: Path, sha: str, *, marker: str | None = None) -> Path:
    """설치기가 만드는 모양 그대로: .git 제외 by-value 복사 + 마커 + 읽기 전용."""
    release = tmp_path / "releases" / sha
    release.mkdir(parents=True)
    for path in sorted(origin.rglob("*")):
        if ".git" in path.relative_to(origin).parts:
            continue
        target = release / path.relative_to(origin)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_bytes(path.read_bytes())
        target.chmod(0o555 if os.access(path, os.X_OK) else 0o444)
    _ = (release / ".origin-sha").write_text(f"{marker or sha}\n", encoding="utf-8")
    (release / ".origin-sha").chmod(0o444)
    return release


def _setup(tmp_path: Path, *, marker: str | None = None) -> tuple[Path, Path]:
    origin, sha = _origin(tmp_path)
    mirror = _mirror(tmp_path, origin, sha)
    return _release(tmp_path, origin, sha, marker=marker), mirror


def test_an_untouched_release_matching_the_tip_verifies(tmp_path: Path) -> None:
    release, mirror = _setup(tmp_path)
    verdict = verify_release(release, git_root=mirror)
    assert verdict.ok, verdict.reason


def test_a_changed_byte_is_a_security_block(tmp_path: Path) -> None:
    """봉인된 트리가 커밋과 한 바이트라도 다르면 그것은 git 에 없는 코드다."""
    release, mirror = _setup(tmp_path)
    (release / "AGENTS.md").chmod(0o644)
    _ = (release / "AGENTS.md").write_text("몰래 고침\n", encoding="utf-8")
    verdict = verify_release(release, git_root=mirror)
    assert not verdict.ok
    assert "AGENTS.md" in verdict.reason


def test_an_extra_file_is_blocked_even_though_git_never_heard_of_it(tmp_path: Path) -> None:
    """tracked 목록만 해시하던 옛 방식이 놓치던 바로 그 경로."""
    release, mirror = _setup(tmp_path)
    _ = (release / "automation" / "sneaked.py").write_text("x = 1\n", encoding="utf-8")
    verdict = verify_release(release, git_root=mirror)
    assert not verdict.ok
    assert "sneaked.py" in verdict.reason


def test_a_missing_file_is_blocked(tmp_path: Path) -> None:
    release, mirror = _setup(tmp_path)
    (release / "automation" / "run.sh").unlink()
    verdict = verify_release(release, git_root=mirror)
    assert not verdict.ok
    assert "run.sh" in verdict.reason


def test_a_symlink_is_blocked(tmp_path: Path) -> None:
    """검증한 바이트와 실제로 열리는 바이트가 갈라지는 유일한 방법을 막는다."""
    release, mirror = _setup(tmp_path)
    (release / "AGENTS.md").unlink()
    (release / "AGENTS.md").symlink_to("/etc/passwd")
    verdict = verify_release(release, git_root=mirror)
    assert not verdict.ok


def test_a_lying_marker_is_blocked(tmp_path: Path) -> None:
    """마커는 신원 주장일 뿐이다 — 디렉터리 이름과 어긋나면 신뢰할 수 없다."""
    release, mirror = _setup(tmp_path, marker="0" * 40)
    verdict = verify_release(release, git_root=mirror)
    assert not verdict.ok
    assert "origin-sha" in verdict.reason


def test_a_release_that_is_not_the_tip_is_blocked(tmp_path: Path) -> None:
    """조상이어도 통과시키지 않는다 — 재조정 타이머가 최신을 설치할 때까지 미룬다."""
    origin, sha = _origin(tmp_path)
    mirror = _mirror(tmp_path, origin, sha)
    release = _release(tmp_path, origin, sha)
    _ = (origin / "next.md").write_text("다음\n", encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "--quiet", "-m", "advance")
    _git(mirror, "fetch", "--quiet", "origin", "main")
    _git(mirror, "update-ref", "refs/remotes/origin/main", _git(origin, "rev-parse", "HEAD"))
    verdict = verify_release(release, git_root=mirror)
    assert not verdict.ok
    assert "tip" in verdict.reason or "origin/main" in verdict.reason


def test_an_unknown_commit_is_blocked(tmp_path: Path) -> None:
    release, mirror = _setup(tmp_path)
    _git(mirror, "update-ref", "refs/remotes/origin/main", "0" * 40, "--no-deref")
    verdict = verify_release(release, git_root=mirror)
    assert not verdict.ok


def test_a_missing_git_object_store_blocks_rather_than_proceeds(tmp_path: Path) -> None:
    """확인할 수 없으면 통과가 아니다 — 주변 시스템 전체가 fail-closed 다."""
    release, _ = _setup(tmp_path)
    verdict = verify_release(release, git_root=tmp_path / "absent")
    assert not verdict.ok


def test_a_writable_release_file_is_blocked(tmp_path: Path) -> None:
    """읽기 전용이 아니면 검증 시점과 사용 시점 사이에 바뀔 수 있다."""
    release, mirror = _setup(tmp_path)
    (release / "AGENTS.md").chmod(0o644)
    verdict = verify_release(release, git_root=mirror)
    assert not verdict.ok


def test_a_lost_exec_bit_is_blocked(tmp_path: Path) -> None:
    """실행 비트는 커밋 모드의 일부다 — 달라지면 같은 트리가 아니다."""
    release, mirror = _setup(tmp_path)
    (release / "automation" / "run.sh").chmod(0o444)
    verdict = verify_release(release, git_root=mirror)
    assert not verdict.ok
    assert "run.sh" in verdict.reason


def test_korean_paths_survive_the_comparison(tmp_path: Path) -> None:
    """ls-tree 기본 인용은 한글 경로를 C-escape 로 바꾼다 — NUL 열거가 아니면 전부 오탐."""
    release, mirror = _setup(tmp_path)
    assert (release / "automation" / "한글.md").is_file()
    assert verify_release(release, git_root=mirror).ok


def test_the_marker_itself_is_the_only_permitted_extra(tmp_path: Path) -> None:
    release, mirror = _setup(tmp_path)
    expected = subprocess.run(
        ("git", "-C", str(mirror), "ls-tree", "-r", "--name-only", "-z", "origin/main"),
        capture_output=True, text=True, check=True,
    ).stdout.split("\0")
    assert ".origin-sha" not in {name for name in expected if name}
    assert verify_release(release, git_root=mirror).ok


def test_the_blob_hash_is_gits_own(tmp_path: Path) -> None:
    """직접 계산한 blob oid 가 git 의 것과 같아야 대조가 의미를 갖는다."""
    release, mirror = _setup(tmp_path)
    data = (release / "AGENTS.md").read_bytes()
    mine = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 - git blob oid
    theirs = subprocess.run(
        ("git", "-C", str(mirror), "rev-parse", "origin/main:AGENTS.md"),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert mine == theirs


def test_a_mirror_git_considers_foreign_is_still_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """자율 경로는 미러를 '남의 저장소'로 보는 환경에서 돈다 — 그래도 읽어야 한다.

    ⑦ 워처의 특권 헬퍼는 `env -i … HOME=/root` 로 파이프라인을 띄운다(약화 변수를
    통째로 없애려는 의도된 선택). 그 결과 git 은 실행 계정의 gitconfig 대신
    `/root/.gitconfig` 를 읽고, ops 소유인 미러를 dubious ownership 으로 거부한다.
    2026-08-03 실측: `DEPLOY-BLOCK: cannot read origin/main from /srv/autophagy-agents`
    가 매 tick 반복되며 승인된 배포가 조용히 멈췄다.

    미러는 읽기 전용 객체 저장소로만 쓰므로(워킹트리를 보지도 실행하지도 않는다)
    호출자가 지정한 그 경로 하나를 명시적으로 안전하다고 선언하는 것이 맞다.
    """
    release, mirror = _setup(tmp_path)
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    verdict = verify_release(release, git_root=mirror)
    assert verdict.ok, verdict.reason
