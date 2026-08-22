"""Obsidian→wiki 큐레이션 — 제안만 하고, 저장은 기존 게이트가 한다.

WHY: Obsidian 에는 사람이 쓴 원천 노트가 2,500건 있는데 위키에는 승인된 정리본이 28건뿐이라
위키 계층이 굶는다. 이 패키지는 그 격차를 후보 제안으로 메운다 — **자동 저장은 하지 않는다**.
초안은 기존 `wiki_cli draft` 게이트로만 나가고 소유자 ✅ 가 있어야 노트가 된다.

여기서 고정하는 불변식 넷:
1. 같은 내용이 이미 위키에 있으면 후보에서 빠진다(저장측 이중 인덱싱 차단 — 조회측은
   `automation/knowledge/rank.py` 가 이미 sha256 로 접는다).
2. patent-sensitive 원천은 후보가 되지 않는다.
3. 주당 상한을 넘겨 제안하지 않는다(ISO 주 경계는 주입 시계로 판정한다).
4. `review_after` 없는 초안은 만들지 않는다.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from automation.wiki_curate.candidates import SourceNote, select_candidates
from automation.wiki_curate.draft import DraftRefused, draft_argv
from automation.wiki_curate.state import StateRefused, remaining_quota, record_proposals

_CLOCK = datetime(2026, 8, 21, tzinfo=timezone.utc)


def _note(ref: str, body: str = "합의한 조건을 적는다.", *, sensitivity: str | None = None,
          event_date: str | None = "2026-05-02", entities: tuple[str, ...] = ("김박사",)) -> SourceNote:
    return SourceNote(
        ref=ref, title=f"{ref} 제목", body=body, tags=("연구",),
        sensitivity=sensitivity, event_date=event_date, entities=entities,
    )


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def test_selection_is_deterministic_and_newest_event_first() -> None:
    notes = (
        _note("b", "두 번째", event_date="2026-01-01"),
        _note("a", "첫 번째", event_date="2026-07-01"),
        _note("c", "세 번째", event_date=None),
    )
    first = select_candidates(notes, existing_digests=frozenset(), limit=10, clock=lambda: _CLOCK)
    second = select_candidates(notes, existing_digests=frozenset(), limit=10, clock=lambda: _CLOCK)
    assert [c.source_ref for c in first] == ["a", "b", "c"]
    assert first == second


def test_patent_sensitive_sources_never_become_candidates() -> None:
    notes = (_note("secret", "특허 초안", sensitivity="patent-sensitive"), _note("ok"))
    picked = select_candidates(notes, existing_digests=frozenset(), limit=10, clock=lambda: _CLOCK)
    assert [c.source_ref for c in picked] == ["ok"]


def test_content_already_represented_in_the_wiki_is_skipped() -> None:
    """저장측 의미 중복 차단 — 같은 내용을 wiki: 와 obsidian: 두 키로 만들지 않는다."""
    notes = (_note("dup", "이미 있는 내용"), _note("new", "새 내용"))
    picked = select_candidates(
        notes, existing_digests=frozenset({_digest("이미 있는 내용")}), limit=10, clock=lambda: _CLOCK
    )
    assert [c.source_ref for c in picked] == ["new"]


def test_empty_sources_yield_no_candidate() -> None:
    assert select_candidates((_note("blank", "   "),), existing_digests=frozenset(), limit=10, clock=lambda: _CLOCK) == ()


def test_every_candidate_carries_review_after_and_its_origin() -> None:
    candidate = select_candidates((_note("origin"),), existing_digests=frozenset(), limit=1, clock=lambda: _CLOCK)[0]
    assert candidate.review_after > _CLOCK.date().isoformat()
    assert date.fromisoformat(candidate.review_after)
    assert "source:origin" in candidate.relations
    assert candidate.entity == ("김박사",)
    assert candidate.event_date == "2026-05-02"


def test_limit_caps_the_batch() -> None:
    notes = tuple(_note(f"n{index}", f"내용 {index}") for index in range(5))
    assert len(select_candidates(notes, existing_digests=frozenset(), limit=2, clock=lambda: _CLOCK)) == 2


def test_weekly_quota_resets_on_the_iso_week_boundary(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    week34 = datetime(2026, 8, 21, tzinfo=timezone.utc)
    week35 = datetime(2026, 8, 25, tzinfo=timezone.utc)
    assert remaining_quota(state, cap=5, clock=lambda: week34) == 5
    record_proposals(state, 5, clock=lambda: week34)
    assert remaining_quota(state, cap=5, clock=lambda: week34) == 0
    assert remaining_quota(state, cap=5, clock=lambda: week35) == 5


def test_state_inside_a_git_checkout_is_refused(tmp_path: Path) -> None:
    """런타임 상태를 추적 체크아웃에 쓰면 ff-pull 이 막힌다 — 규약상 금지."""
    (tmp_path / ".git").mkdir()
    with pytest.raises(StateRefused):
        remaining_quota(tmp_path / "nested" / "state.json", cap=5, clock=lambda: _CLOCK)


def test_draft_argv_uses_the_existing_gate_with_v2_flags(tmp_path: Path) -> None:
    candidate = select_candidates((_note("origin"),), existing_digests=frozenset(), limit=1, clock=lambda: _CLOCK)[0]
    body_file = tmp_path / "body.md"
    argv = draft_argv(candidate, cli_path=Path("/live/wiki/scripts/wiki_cli.py"), body_file=body_file)
    assert argv[:2] == ["/live/wiki/scripts/wiki_cli.py", "draft"]
    assert "--provenance" in argv and argv[argv.index("--provenance") + 1] == "inferred"
    assert argv[argv.index("--entity") + 1] == "김박사"
    assert argv[argv.index("--relations") + 1] == "source:origin"
    assert argv[argv.index("--event-date") + 1] == "2026-05-02"
    assert argv[argv.index("--review-after") + 1] == candidate.review_after
    assert argv[argv.index("--body-file") + 1] == str(body_file)
    assert "--authority" in argv and argv[argv.index("--authority") + 1] != "strict"


def test_draft_refuses_a_candidate_without_review_after() -> None:
    candidate = select_candidates((_note("origin"),), existing_digests=frozenset(), limit=1, clock=lambda: _CLOCK)[0]
    with pytest.raises(DraftRefused):
        draft_argv(
            replace(candidate, review_after=""),
            cli_path=Path("/live/wiki/scripts/wiki_cli.py"),
            body_file=Path("/tmp/body.md"),
        )


def test_a_source_already_curated_is_skipped_even_though_its_wiki_body_differs() -> None:
    """증류된 노트의 본문은 원천과 다르다 — digest 비교만으로는 같은 원천을 계속 재제안한다."""
    notes = (_note("projects/kimm.md"),)
    picked = select_candidates(
        notes,
        existing_digests=frozenset(),
        existing_origins=frozenset({"projects/kimm.md"}),
        limit=10,
        clock=lambda: _CLOCK,
    )
    assert picked == ()
