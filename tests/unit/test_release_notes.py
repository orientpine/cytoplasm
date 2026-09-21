"""릴리스 변경 상세 메시지: 번들별로 보이고, 하나도 생략하지 않고, 한도에서 나뉜다."""
from __future__ import annotations

import re

from automation.release_notes import (
    ReleaseCommit,
    detail_body,
    major_note,
    render_detail_messages,
)
from automation.release_plan import ReleasePlan
from automation.release_spec_message import MESSAGE_LIMIT

_HEAD = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
#: 렌더러가 절대 만들어서는 안 되는 문자열 — 원문 항목이 사라졌다는 신호다.
_FORBIDDEN = ("omitted", "생략", "…", "...")


def _plan(
    commits: tuple[ReleaseCommit, ...], *, signals: tuple[str, ...] = ()
) -> ReleasePlan:
    return ReleasePlan(
        version="v1.2.3",
        base="b" * 40,
        head=_HEAD,
        tree_digest="c" * 64,
        changed_paths=tuple(path for commit in commits for path in commit.paths),
        surface_digests=(),
        commit_titles=tuple(commit.subject for commit in commits),
        major_signals=signals,
        commits=commits,
    )


def _commit(index: int, subject: str, bundles: tuple[str, ...]) -> ReleaseCommit:
    return ReleaseCommit(
        sha12=f"{index:012x}",
        subject=subject,
        paths=("some/path.py",) if bundles else (),
        bundles=bundles,
    )


def _bodies(messages: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(message.split("\n", 1)[1] for message in messages)


def test_each_bundle_section_lists_its_own_commits_and_shared_ones_appear_once() -> None:
    """Given commits on one, two and no bundles / When the detail messages render /
    Then every commit shows up exactly once under a section that names its bundles."""
    commits = (
        _commit(1, "스킬 본문을 고친다", ("skill:demo",)),
        _commit(2, "홈 래퍼를 고친다", ("home:automation/pkg",)),
        _commit(3, "스킬과 런타임을 함께 고친다", ("skill:demo", "runtime")),
        _commit(4, "파일을 건드리지 않는다", ()),
    )

    messages = render_detail_messages(_plan(commits))
    joined = "\n".join(messages)

    assert "### skill:demo" in joined
    assert "### home:automation/pkg" in joined
    assert "### 여러 번들" in joined
    assert "### 공통·기타" in joined
    for commit in commits:
        assert joined.count(commit.subject) == 1
        assert commit.sha12 in joined
    shared = next(line for line in joined.splitlines() if commits[2].subject in line)
    assert "skill:demo" in shared
    assert "runtime" in shared


def test_sixty_commits_survive_the_split_without_a_single_dropped_line() -> None:
    """Given a release far past one Discord message / When the detail messages render /
    Then every subject is present exactly once and no message exceeds the limit."""
    commits = tuple(
        _commit(index, f"변경 {index:02d} " + "가" * 60, (f"skill:demo{index % 3}",))
        for index in range(60)
    )

    messages = render_detail_messages(_plan(commits))
    joined = "\n".join(messages)

    assert len(messages) > 1
    for index, message in enumerate(messages, start=1):
        assert len(message) <= MESSAGE_LIMIT
        assert message.startswith(
            f"[release] v1.2.3 변경 상세 ({index}/{len(messages)}) — 기준 {_HEAD[:12]}\n"
        )
    for commit in commits:
        assert joined.count(commit.subject) == 1
    assert not any(token in joined for token in _FORBIDDEN)
    assert re.search(r"\+\d+", joined) is None


def test_a_line_longer_than_one_message_is_wrapped_instead_of_dropped() -> None:
    """Given a single commit subject longer than a whole message / When it renders /
    Then its characters survive across messages in order."""
    subject = "긴제목" * 900
    commits = (_commit(1, subject, ("repo",)),)

    messages = render_detail_messages(_plan(commits))

    assert len(messages) > 1
    assert all(len(message) <= MESSAGE_LIMIT for message in messages)
    assert subject in "".join(_bodies(messages)).replace("\n", "")


def test_the_detail_header_attributes_the_release_without_repeating_the_card() -> None:
    """Given detail messages / When they render / Then each carries the release and
    head, and none repeats the card's bundle list or approval instructions."""
    commits = tuple(
        _commit(index, f"변경 {index} " + "나" * 60, ("skill:demo",)) for index in range(40)
    )

    messages = render_detail_messages(_plan(commits))
    joined = "\n".join(messages)

    assert len(messages) > 1
    assert all("v1.2.3" in message and _HEAD[:12] in message for message in messages)
    assert "배포 번들" not in joined
    assert "승인 방법" not in joined
    assert "승인 바인딩" not in joined


def test_a_release_without_commits_still_renders_one_readable_message() -> None:
    """Given an empty commit range / When the detail body renders /
    Then it says so instead of producing an empty approval record."""
    messages = render_detail_messages(_plan(()))

    assert len(messages) == 1
    assert detail_body(_plan(())).strip()
    assert "커밋 없음" in messages[0]


def test_major_signals_become_one_operator_line_for_the_card() -> None:
    """Given machine-contract signals / When the operator line renders /
    Then it names every signal, and an ordinary release renders no line."""
    signals = ("automation/interop/approval_surface.py:POLICY_VERSION",)

    assert major_note(_plan((), signals=signals)) == (
        "MAJOR: 운영자 조치 필요 — automation/interop/approval_surface.py:POLICY_VERSION"
    )
    assert major_note(_plan(())) == ""


def test_two_commits_sharing_a_subject_both_keep_their_own_line() -> None:
    """Given a range where a subject repeats (real history has merge commits that do) /
    When the details render / Then both commits stay, each with its own sha."""
    commits = (
        _commit(1, "같은 제목", ("repo",)),
        _commit(2, "같은 제목", ("repo",)),
    )

    joined = "\n".join(render_detail_messages(_plan(commits)))

    assert joined.count("- 같은 제목 (") == 2
    assert all(commit.sha12 in joined for commit in commits)
