from __future__ import annotations

import pytest

from automation.memory_routing import classify_memory_request


@pytest.mark.parametrize(
    "text",
    [
        "기억해",
        "기억해줘",
        "앞으로도 이렇게",
    ],
)
def test_conservative_wiki_without_memory_md_when_explicit_request_is_ambiguous(
    text: str,
) -> None:
    # Given: an explicit but content-free or referential memory request.
    # When: the request is classified.
    route = classify_memory_request(text)

    # Then: wiki remains the default and MEMORY.md is never guessed.
    assert route.canonical == "wiki"
    assert "memory_md" not in route.co_write
    assert route.reason == "uncertain-conservative"


def test_wiki_when_explicit_request_contains_project_knowledge() -> None:
    # Given: explicit project knowledge that is not a global personal preference.
    text = "이 프로젝트의 샘플 식별자 접두사는 AX야. 다음 분석에서도 참조하도록 기억해줘"

    # When: the request is classified.
    route = classify_memory_request(text)

    # Then: the RAG-ingested wiki is the sole persistence target.
    assert route.canonical == "wiki"
    assert route.co_write == ()
    assert route.reason == "explicit-memory-wiki"


def test_wiki_without_memory_md_when_project_knowledge_is_long() -> None:
    # Given: a long project-specific memory request containing preference-like words.
    text = (
        "기억해줘. 우리 프로젝트에서는 실험군 AX-17의 전처리 결과를 기준선으로 사용하고, "
        "샘플별 보정 계수와 장비 교정 이력을 함께 비교하며, 다음 분기 재현성 검토 전까지 "
        "이 분석 규칙과 예외 목록을 프로젝트 지식으로 유지해야 해. 결과 보고서의 표 형식은 "
        "열 순서를 샘플, 배치, 보정값, 판정으로 고정하는 것을 선호해."
    )

    # When: the request is classified.
    route = classify_memory_request(text)

    # Then: length and project scope prevent a MEMORY.md co-write.
    assert route.canonical == "wiki"
    assert "memory_md" not in route.co_write


def test_memory_md_co_write_when_preference_is_short_stable_and_global() -> None:
    # Given: a short, stable preference useful across conversations.
    text = "나는 답변을 짧은 한국어로 받는 것을 항상 선호해. 기억해줘"

    # When: the request is classified.
    route = classify_memory_request(text)

    # Then: wiki is canonical and MEMORY.md receives the narrow co-write.
    assert route.canonical == "wiki"
    assert route.co_write == ("memory_md",)
    assert route.reason == "stable-global-preference"


def test_skill_without_memory_md_when_request_is_reusable_procedure() -> None:
    # Given: an explicit reusable procedure.
    text = "보고서를 만들 때는 초안 검토, 민감도 확인, 승인 요청 순서로 진행하는 절차를 기억해줘"

    # When: the request is classified.
    route = classify_memory_request(text)

    # Then: the procedure belongs only to a skill.
    assert route.canonical == "skill"
    assert route.co_write == ()
    assert route.never_persist is False
    assert route.reason == "reusable-procedure"


def test_tasks_and_never_persist_when_status_expires_within_seven_days() -> None:
    # Given: an owner status explicitly bounded to this week.
    text = "이번 주 금요일까지 출장 중이라 답장이 늦어. 기억해줘"

    # When: the request is classified.
    route = classify_memory_request(text)

    # Then: the short-lived status is task context, never durable memory.
    assert route.canonical == "tasks"
    assert route.co_write == ()
    assert route.never_persist is True
    assert route.reason == "temporary-status"


def test_sensitive_flag_preserves_route_and_requires_approval() -> None:
    # Given: a stable preference already flagged by the sensitivity boundary.
    text = "나는 답변을 짧은 한국어로 받는 것을 항상 선호해. 기억해줘"

    # When: the request is classified with a sensitivity tag.
    route = classify_memory_request(text, sensitivity=frozenset({"patent-sensitive"}))

    # Then: classification remains available but persistence requires approval.
    assert route.canonical == "wiki"
    assert route.co_write == ("memory_md",)
    assert route.needs_sensitive_approval is True
    assert route.reason == "sensitive-needs-approval"


def test_none_and_never_persist_when_request_is_not_explicit_memory() -> None:
    # Given: ordinary text without an explicit memory request.
    text = "이번 실험 결과를 설명해줘"

    # When: the text is classified.
    route = classify_memory_request(text)

    # Then: no persistence target is selected.
    assert route.canonical == "none"
    assert route.co_write == ()
    assert route.never_persist is True
    assert route.reason == "uncertain-conservative"
