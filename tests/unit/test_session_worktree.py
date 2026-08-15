"""세션 워크트리 헬퍼 — 낡은 기반에서 시작하지 않고, 안 착지한 일을 지우지 않는다.

2026-08-03 실측으로 나온 두 사고가 이 스크립트의 존재 이유다.

**시작 쪽.** 세션마다 브랜치를 따는 동안 `origin/main`이 세 번 전진했다
(`c58ea5a`→`da6d03c`→`c119866`). 로컬 `refs/remotes/origin/main`은 fetch 하기 전까지
움직이지 않으므로, fetch 없이 브랜치를 따면 **낡은 지점에서 시작한 줄도 모르고**
작업하게 된다. 이 리포에는 그렇게 옛 체크아웃이 최신 결정을 덮어써 배포가 404로
실패한 선례가 있다(2026-07-21). 그래서 fetch 실패는 경고가 아니라 정지다 —
"조용히 캐시된 ref를 쓴다"가 정확히 그 사고의 모양이기 때문이다.

**종료 쪽.** 이미 머지된 PR의 브랜치에 증적 커밋을 push했더니 브랜치에는 올라갔지만
착지하지 못했다. push가 성공했으므로 아무 신호도 없었고, 브랜치를 정리하면서
`origin/main..<브랜치>`를 세어보지 않았다면 65줄짜리 QA 증적이 조용히 사라졌을
것이다. 그래서 미착지 커밋이 있으면 제거를 거부한다.

**손댈 경로의 최근 이력.** 같은 파일을 다른 세션이 방금 고쳤는지는 ref가 최신이어도
알 수 없다. 오늘 `docs/features.md`에서 정확히 그래서 남의 묶음 헤더를 덮어썼다.
`--paths`를 주면 그 경로들의 최근 커밋을 시작 시점에 보여준다 — 「다른 세션 작업
덮어쓰기 방지」가 요구하는 확인을 기억이 아니라 출력으로 만든다.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "automation" / "worktree.sh"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _run(cwd: Path, *args: str, env: dict[str, str] | None = None):
    environment = dict(os.environ)
    environment.setdefault("WORKTREE_ROOT", str(cwd.parent / "wt"))
    if env:
        environment.update(env)
    return subprocess.run(
        ("bash", str(_SCRIPT), *args), cwd=cwd, capture_output=True, text=True,
        check=False, env=environment,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """원격 하나와 그것을 복제한 개발 체크아웃."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "--quiet", "--bare", "--initial-branch=main")
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "--quiet", "--initial-branch=main")
    _git(seed, "config", "user.email", "t@e.i")
    _git(seed, "config", "user.name", "t")
    _ = (seed / "README.md").write_text("seed\n", encoding="utf-8")
    (seed / "docs").mkdir()
    _ = (seed / "docs" / "board.md").write_text("board\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "--quiet", "-m", "seed")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "--quiet", "origin", "main")

    dev = tmp_path / "dev"
    _git(tmp_path, "clone", "--quiet", str(origin), str(dev))
    _git(dev, "config", "user.email", "t@e.i")
    _git(dev, "config", "user.name", "t")
    return dev


def _advance_origin(repo: Path, message: str, path: str = "docs/board.md") -> str:
    """다른 세션이 origin/main 을 전진시킨 상황."""
    other = repo.parent / f"other-{abs(hash(message)) % 10000}"
    _git(repo.parent, "clone", "--quiet", str(repo.parent / "origin"), str(other))
    _git(other, "config", "user.email", "o@e.i")
    _git(other, "config", "user.name", "o")
    target = other / path
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = target.write_text(target.read_text(encoding="utf-8") + message + "\n", encoding="utf-8")
    _git(other, "add", "-A")
    _git(other, "commit", "--quiet", "-m", message)
    _git(other, "push", "--quiet", "origin", "main")
    return _git(other, "rev-parse", "HEAD")


def test_start_refuses_when_it_cannot_refresh_the_base(repo: Path) -> None:
    """캐시된 ref 를 조용히 쓰는 것이 정확히 2026-07-21 사고의 모양이다."""
    _git(repo, "remote", "set-url", "origin", str(repo.parent / "absent"))
    result = _run(repo, "start", "s1")
    assert result.returncode != 0
    assert "fetch" in result.stderr.lower()
    assert not (repo.parent / "wt" / "s1").exists()


def test_start_bases_the_worktree_on_the_freshly_fetched_tip(repo: Path) -> None:
    """fetch 전 로컬 ref 는 낡아 있다 — 시작점은 방금 가져온 tip 이어야 한다."""
    stale = _git(repo, "rev-parse", "refs/remotes/origin/main")
    fresh = _advance_origin(repo, "landed-while-you-were-away")
    assert stale != fresh
    result = _run(repo, "start", "s1")
    assert result.returncode == 0, result.stderr
    assert _git(repo.parent / "wt" / "s1", "rev-parse", "HEAD") == fresh


def test_start_reports_what_landed_since_the_local_ref(repo: Path) -> None:
    """무엇을 놓치고 있었는지 말해주지 않으면 최신화는 조용한 사실일 뿐이다."""
    _ = _advance_origin(repo, "other-session-work")
    result = _run(repo, "start", "s1")
    assert result.returncode == 0, result.stderr
    assert "other-session-work" in result.stdout


def test_start_shows_recent_commits_touching_the_paths_you_name(repo: Path) -> None:
    """ref 가 최신이어도 '그 파일을 방금 누가 고쳤나'는 따로 봐야 안다."""
    _ = _advance_origin(repo, "someone-edited-the-board", path="docs/board.md")
    result = _run(repo, "start", "s1", "--paths", "docs/board.md")
    assert result.returncode == 0, result.stderr
    assert "someone-edited-the-board" in result.stdout
    assert "docs/board.md" in result.stdout


def test_start_refuses_a_name_already_in_use(repo: Path) -> None:
    assert _run(repo, "start", "s1").returncode == 0
    again = _run(repo, "start", "s1")
    assert again.returncode != 0
    assert "s1" in again.stderr


def test_finish_refuses_to_drop_work_that_never_landed(repo: Path) -> None:
    """오늘 65줄짜리 QA 증적이 이 검사 하나로 살아남았다."""
    assert _run(repo, "start", "s1").returncode == 0
    wt = repo.parent / "wt" / "s1"
    _ = (wt / "evidence.txt").write_text("증적\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "--quiet", "-m", "never landed")
    result = _run(repo, "finish", "s1")
    assert result.returncode != 0
    assert "never landed" in result.stderr or "1" in result.stderr
    assert wt.exists(), "착지 못한 커밋이 있는데 워크트리를 지웠다"


def test_finish_refuses_when_the_worktree_is_dirty(repo: Path) -> None:
    assert _run(repo, "start", "s1").returncode == 0
    wt = repo.parent / "wt" / "s1"
    _ = (wt / "scratch.txt").write_text("작업 중\n", encoding="utf-8")
    result = _run(repo, "finish", "s1")
    assert result.returncode != 0
    assert wt.exists()


def test_finish_removes_a_worktree_whose_work_all_landed(repo: Path) -> None:
    assert _run(repo, "start", "s1").returncode == 0
    wt = repo.parent / "wt" / "s1"
    result = _run(repo, "finish", "s1")
    assert result.returncode == 0, result.stderr
    assert not wt.exists()
    assert "session/s1" not in _git(repo, "branch", "--list", "session/s1")


def test_the_helper_never_deletes_a_remote_branch(repo: Path) -> None:
    """원격 삭제는 공유 영향이라 사람이 판단한다 — 자동화가 조용히 할 일이 아니다."""
    body = _SCRIPT.read_text(encoding="utf-8")
    assert "push origin --delete" not in body
    assert "push --delete" not in body
