"""완결 대상 판정 — 순서가 곧 정책이므로 각 갈림길을 따로 못박는다.

e2e(`test_release_complete.py`)는 완결기가 대상을 어떻게 **쓰는지**를 고정하고, 여기서는
무엇을 대상으로 **고르는지**를 고정한다. 특히 태그가 없거나 한 커밋에 릴리스 번호가 둘인
경우의 보류는 e2e 로는 드러나지 않는다 — 그런 이력을 만드는 일 자체가 사고이기 때문이다.
"""
from __future__ import annotations

import os
import subprocess

import pytest
from pathlib import Path
from typing import Final

from automation.release_completion_target import (
    CompletionFacts,
    NoWork,
    Reconcile,
    decide,
    gather,
    main,
)

_TAGGED: Final = "1" * 40
_TIP: Final = "2" * 40


def _facts(
    *,
    head: str = _TIP,
    tagged: str | None = _TAGGED,
    tag_names: tuple[str, ...] = ("v9.9.9",),
    completed: bool = False,
) -> CompletionFacts:
    return CompletionFacts(
        head=head, tagged=tagged, tag_names=tag_names, completed=completed
    )


def test_an_unfinished_tagged_release_behind_the_tip_is_the_target() -> None:
    assert decide(_facts()) == Reconcile(_TAGGED, "v9.9.9")


def test_a_history_without_release_tags_has_no_target() -> None:
    assert decide(_facts(tagged=None, tag_names=())) == NoWork("NO-RELEASE-TAG")


def test_a_tagged_tip_is_left_to_the_approval_path() -> None:
    """팁이 곧 릴리스면 완결은 승인 경로가 소유한다 — 두 경로가 같은 sha 를 다투지 않는다."""
    assert decide(_facts(head=_TAGGED)) == NoWork("TIP-IS-RELEASE")


def test_a_commit_carrying_two_release_numbers_is_held() -> None:
    """무엇을 완결했다고 적을지 기계가 고를 수 없다 — 사람이 고른다."""
    assert decide(_facts(tag_names=("v9.9.9", "v9.10.0"))) == NoWork("VERSION-AMBIGUOUS")


def test_a_commit_without_a_release_number_is_held() -> None:
    """describe 는 prerelease 접미 태그도 집어 온다 — 그 커밋은 릴리스가 아니다."""
    assert decide(_facts(tag_names=())) == NoWork("NO-RELEASE-TAG")


def test_a_completed_release_is_not_reconciled_again() -> None:
    assert decide(_facts(completed=True)) == NoWork("ALREADY-COMPLETED")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(cwd), *args),
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    ).stdout.strip()


def _repo_with_tag(tmp_path: Path, tag: str, *, commits: int) -> tuple[Path, str]:
    origin = tmp_path / "origin.git"
    _ = subprocess.run(
        ("git", "init", "--bare", "-b", "main", str(origin)), check=True, capture_output=True
    )
    source = tmp_path / "source"
    _ = subprocess.run(("git", "clone", str(origin), str(source)), check=True, capture_output=True)
    _ = _git(source, "config", "user.name", "completion-target-test")
    _ = _git(source, "config", "user.email", "completion-target@example.invalid")
    _ = _git(source, "config", "commit.gpgsign", "false")
    _ = (source / "tracked").write_text("clean\n", encoding="utf-8")
    _ = _git(source, "add", "tracked")
    _ = _git(source, "commit", "-m", "initial")
    _ = _git(source, "push", "-u", "origin", "main")
    tagged = _git(source, "rev-parse", "HEAD")
    _ = _git(source, "tag", "-a", tag, "-m", tag)
    for index in range(commits):
        _ = (source / f"after-{index}").write_text("later\n", encoding="utf-8")
        _ = _git(source, "add", f"after-{index}")
        _ = _git(source, "commit", "-m", f"after {index}")
    _ = _git(source, "push", "origin", "main")
    return source, tagged


def test_gather_reads_the_nearest_release_tag_behind_origin_main(tmp_path: Path) -> None:
    source, tagged = _repo_with_tag(tmp_path, "v9.9.9", commits=2)

    facts = gather(source, tmp_path / "state")

    assert facts.head == _git(source, "rev-parse", "origin/main")
    assert facts.tagged == tagged
    assert facts.tag_names == ("v9.9.9",)
    assert facts.completed is False


def test_gather_reports_a_prerelease_tag_as_carrying_no_release_number(
    tmp_path: Path,
) -> None:
    source, tagged = _repo_with_tag(tmp_path, "v9.9.9-rc1", commits=2)

    facts = gather(source, tmp_path / "state")

    assert facts.tagged == tagged
    assert facts.tag_names == ()
    assert decide(facts) == NoWork("NO-RELEASE-TAG")


def test_gather_sees_the_completion_marker(tmp_path: Path) -> None:
    source, tagged = _repo_with_tag(tmp_path, "v9.9.9", commits=1)
    state = tmp_path / "state"
    (state / "completed").mkdir(parents=True)
    _ = (state / "completed" / tagged).write_text("2026-09-09T00:00:00Z\n", encoding="utf-8")

    assert gather(source, state).completed is True


def test_main_prints_the_target_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source, tagged = _repo_with_tag(tmp_path, "v9.9.9", commits=2)

    code = main(["--repo", str(source), "--state", str(tmp_path / "state")])

    assert code == 0
    assert capsys.readouterr().out.strip() == f"{tagged} v9.9.9"


def test_main_prints_only_a_reason_when_there_is_nothing_to_do(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source, _tagged = _repo_with_tag(tmp_path, "v9.9.9", commits=0)

    code = main(["--repo", str(source), "--state", str(tmp_path / "state")])

    assert code == 1
    assert capsys.readouterr().out.strip() == "TIP-IS-RELEASE"
