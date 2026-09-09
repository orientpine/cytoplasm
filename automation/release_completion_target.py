"""완결기가 **어느 릴리스를 완결할 것인가**를 정하는 단일 판정.

승인은 sha 에 묶이지만 `release_complete.sh` 는 매 틱 origin/main HEAD 하나만 들고 물었다.
그래서 태그를 자른 직후 새 커밋이 머지되면, 소유자 ✅ 를 받고 서명 태그까지 잘려 **노드가
실제로 돌리고 있는** 릴리스가 영영 완결되지 못한다 — 2026-09-08 v1.6.3 실측: 결정이 매 틱
`live request is bound to a different HEAD` 로 서서 전량 반영이 재개되지 않았고,
`completed/<sha>` 마커가 없어 `release_applied_notice` 스윕이 그 릴리스를 보지 못해 적용
완료 DM 도 유실됐다.

대상은 **origin/main 에서 도달 가능한 가장 가까운 릴리스 태그의 커밋**이다. 팁이 곧
릴리스인 정상 경로에서는 대상이 head 와 같아 `NoWork` 이므로 기존 경로는 그대로 돈다.

이 판정은 새 인가를 만들지 않는다: 태그는 이미 잘려 있고 남은 일은 전량 반영과 마커뿐이라
호출부가 `release.sh` 를 부를 이유가 없다. 옛 ✅ 로 새 tip 을 인가하는 문은
`release_approval.cmd_decision` 의 HEAD 불일치 거부가 계속 잠근다.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, assert_never

from automation.release_applied_notice import release_tags

#: `git describe` 의 탐색 범위. 무엇이 릴리스 번호인지는 `release_tags` 가 단독으로 정한다.
_DESCRIBE_MATCH: Final = "v[0-9]*.[0-9]*.[0-9]*"


@dataclass(frozen=True, slots=True)
class Reconcile:
    """인가됐으나 완결되지 않은 릴리스 — 전량 반영과 완결 마커가 남았다."""

    sha: str
    version: str


@dataclass(frozen=True, slots=True)
class NoWork:
    """할 일 없음. 사유는 판독용이고 호출부는 조용히 넘어간다."""

    reason: str


Decision = Reconcile | NoWork


@dataclass(frozen=True, slots=True)
class CompletionFacts:
    """판정에 쓰이는 사실 전부 — 여기 없는 것은 판정에 쓰이지 않는다."""

    head: str
    tagged: str | None
    tag_names: tuple[str, ...]
    completed: bool


def decide(facts: CompletionFacts) -> Decision:
    """순수 판정. 순서가 곧 정책이라 앞선 조건이 뒤를 가린다."""
    if facts.tagged is None:
        return NoWork("NO-RELEASE-TAG")
    if facts.tagged == facts.head:
        return NoWork("TIP-IS-RELEASE")
    tags = facts.tag_names
    if len(tags) != 1:
        # describe 는 prerelease 접미 태그도 집어 오므로 릴리스 번호가 없을 수 있고,
        # 한 커밋에 번호가 둘이면 무엇을 완결했다고 적을지 사람이 골라야 한다.
        return NoWork("VERSION-AMBIGUOUS" if tags else "NO-RELEASE-TAG")
    if facts.completed:
        return NoWork("ALREADY-COMPLETED")
    return Reconcile(facts.tagged, tags[0])


def _git(repo: Path, *args: str) -> str | None:
    completed = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _nearest_release_commit(repo: Path, head: str) -> str | None:
    """태그가 하나도 없는 이력은 사고가 아니라 정상이므로 None 으로 답한다."""
    described = _git(
        repo, "describe", "--tags", "--abbrev=0", "--match", _DESCRIBE_MATCH, head
    )
    if described is None:
        return None
    return _git(repo, "rev-parse", f"{described}^{{}}")


def gather(repo: Path, state: Path) -> CompletionFacts:
    """워크트리의 git 사실과 완결 마커만 읽는다 — 노드를 찌르지 않는다."""
    head = _git(repo, "rev-parse", "origin/main") or ""
    tagged = _nearest_release_commit(repo, head) if head else None
    return CompletionFacts(
        head=head,
        tagged=tagged,
        tag_names=release_tags(repo, tagged) if tagged else (),
        completed=bool(tagged) and (state / "completed" / str(tagged)).exists(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """rc 0 이면 stdout 에 `<sha> <version>`, rc 1 이면 사유만 — 호출부는 rc 로 분기한다."""
    parser = argparse.ArgumentParser(prog="release-completion-target")
    _ = parser.add_argument("--repo", required=True)
    _ = parser.add_argument("--state", required=True)
    args = parser.parse_args(argv)
    decision = decide(gather(Path(str(args.repo)), Path(str(args.state))))
    match decision:
        case Reconcile(sha, version):
            print(f"{sha} {version}")
            return 0
        case NoWork(reason):
            print(reason)
            return 1
        case unreachable:
            assert_never(unreachable)


if __name__ == "__main__":
    sys.exit(main())
