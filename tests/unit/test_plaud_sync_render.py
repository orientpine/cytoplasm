from __future__ import annotations

from dataclasses import replace

import pytest

from automation.plaud_sync.model import PlaudSyncRecord
from automation.plaud_sync.render import (
    MAX_MESSAGE_CHARS,
    RENDER_VERSION,
    PlaudRenderError,
    render_plaud_approval,
    summary_preview,
)


_BASE = PlaudSyncRecord(
    version=1,
    recording_id="rec-001",
    recorded_at="2026-09-01T08:00:00Z",
    note_relpath="000_PARA/Area/Lifelog/2026/2026-09-01-standup--abcdef123456.md",
    note_title="standup (2026-09-01)",
    body_sha256="a" * 64,
    action_hash=f"sha256:{'b' * 64}",
    status="planned",
    kind="obsidian-write",
    surface="agent-chat-thread",
    channel_id="",
    policy_version=8,
    message_id=None,
    created_at="2026-09-01T09:00:00Z",
    approved_at=None,
    written_at=None,
    remote_ref=None,
    note_content_sha256=None,
    last_block_reason=None,
)


def _record(**overrides: object) -> PlaudSyncRecord:
    return replace(_BASE, **overrides)


def test_card_carries_hash_id_path_and_version() -> None:
    content = render_plaud_approval(_record())
    assert RENDER_VERSION in content
    assert "rec-001" in content
    assert f"sha256:{'b' * 64}" in content
    assert "000_PARA/Area/Lifelog/2026/2026-09-01-standup--abcdef123456.md" in content
    assert "\u2705" in content
    assert "\u26d4" in content


def test_card_length_is_fail_closed() -> None:
    oversized = _record(note_relpath="000_PARA/Area/Lifelog/2026/" + "x" * MAX_MESSAGE_CHARS)
    with pytest.raises(PlaudRenderError):
        _ = render_plaud_approval(oversized)


_SEVEN_LINE_BODY = (
    "## 요약\n\n- 첫째 줄\n- 둘째 줄\n- 셋째 줄\n- 넷째 줄\n- 다섯째 줄\n- 여섯째 줄\n- 일곱째 줄\n\n"
    "## 전문\n\n[00:00 · 화자1] 안녕하세요.\n\n---\n\n출처: PLAUD `rec-001`"
)


def test_preview_takes_the_first_five_summary_lines() -> None:
    assert summary_preview(_SEVEN_LINE_BODY).splitlines() == [
        "- 첫째 줄", "- 둘째 줄", "- 셋째 줄", "- 넷째 줄", "- 다섯째 줄",
    ]


def test_preview_falls_back_to_the_transcript_when_the_summary_is_a_placeholder() -> None:
    body = (
        "## 요약\n\n- (요약 없음)\n\n## 전문\n\n[00:00 · 화자1] 첫 발화.\n"
        "[00:05 · 화자2] 둘째 발화.\n\n---\n\n출처: PLAUD `rec-001`"
    )
    assert summary_preview(body).splitlines() == [
        "[00:00 · 화자1] 첫 발화.", "[00:05 · 화자2] 둘째 발화.",
    ]


def test_preview_splits_one_paragraph_into_sentences_and_clips_long_ones() -> None:
    body = (
        "## 요약\n\n### 회의 개요\n첫 문장입니다. 둘째 문장입니다! 셋째 문장입니다? "
        + "넷" * 200 + ". 다섯째. 여섯째.\n\n## 전문\n\n- (전문 없음)"
    )
    lines = summary_preview(body).splitlines()
    assert lines[:4] == ["회의 개요", "첫 문장입니다.", "둘째 문장입니다!", "셋째 문장입니다?"]
    assert len(lines) == 5
    assert lines[4].endswith("…") and len(lines[4]) <= 160


def test_preview_is_empty_when_neither_section_has_content() -> None:
    assert summary_preview("## 요약\n\n- (요약 없음)\n\n## 전문\n\n- (전문 없음)\n") == ""


def test_card_quotes_the_preview_and_carries_render_version_three() -> None:
    content = render_plaud_approval(_record(), preview="- 첫째 줄\n- 둘째 줄")
    assert RENDER_VERSION == "plaud-sync-render-v3"
    assert "> - 첫째 줄\n> - 둘째 줄" in content
    assert "내용 미리보기" in content


def test_card_without_a_preview_says_so_instead_of_leaving_a_gap() -> None:
    assert "> (미리보기 없음)" in render_plaud_approval(_record())


def test_preview_drops_image_markdown_and_keeps_the_text() -> None:
    # 2026-09-02 실측: Plaud 요약이 포스터 이미지 마크다운으로 시작해 미리보기 1줄을 먹었다.
    body = (
        "## 요약\n\n![PLAUD NOTE](permanent/abc/summary_poster/card.png)\n"
        "직장 동료들이 식사를 함께한다.\n\n## 전문\n\n- (전사 없음)"
    )
    assert summary_preview(body).splitlines() == ["직장 동료들이 식사를 함께한다."]


def test_preview_continues_past_plauds_own_headings_and_rules() -> None:
    # 2026-09-02 실측: Plaud 요약은 자체 `## 소제목` 과 `------------` 를 쓴다 — 거기서 끊기면 2줄만 남는다.
    body = (
        "## 요약\n\n개요 문장.\n\n------------\n## 일상 잡담\n- 첫 항목.\n- 둘째 항목.\n\n"
        "## Action Items\n- 할 일.\n\n## 전문\n\n[00:00 · Speaker 1] 안녕.\n\n---\n\n출처: PLAUD 녹음 x"
    )
    assert summary_preview(body).splitlines() == [
        "개요 문장.", "일상 잡담", "- 첫 항목.", "- 둘째 항목.", "Action Items",
    ]


# ---- v3 (2026-09-04, B안): 한눈에 줄을 먼저 인용하고 접힌 전문의 '> ' 를 벗긴다 ----

_V2_BODY = (
    "---\ntags: [lifelog, lifelog/업무]\ntitle: t\nsource: PLAUD 녹음 x\n"
    "created: 2026-09-02T09:02:00\nmodified: 2026-09-02T09:02:00\n---\n\n"
    "## 한눈에\n\n- 녹음:: 2026-09-02 (수) 09:02 · 30분 · 화자 2명\n- 주제:: #lifelog/업무\n"
    "- 사람:: [[김철수]]\n\n"
    "## 요약\n\n첫 문장. 둘째 문장. 셋째 문장.\n\n"
    "## 결정 · 할 일\n\n- 결정: 세미나 참석 [12:40]\n\n"
    "## 전문\n\n> [!quote]- 전문 펼치기 (1 발화)\n> [00:00 · 화자1] 안녕.\n\n---\n\n출처: PLAUD 녹음 x"
)


def test_preview_quotes_the_glance_lines_first_and_fills_from_the_summary() -> None:
    assert summary_preview(_V2_BODY).splitlines() == [
        "- 녹음:: 2026-09-02 (수) 09:02 · 30분 · 화자 2명",
        "- 주제:: #lifelog/업무",
        "- 사람:: [[김철수]]",
        "첫 문장.",
        "둘째 문장.",
    ]


def test_preview_never_quotes_frontmatter_or_the_decisions_section() -> None:
    preview = summary_preview(_V2_BODY)
    assert "tags:" not in preview and "title:" not in preview
    assert "세미나 참석" not in preview


def test_preview_unquotes_the_collapsed_transcript_when_nothing_else_has_content() -> None:
    body = (
        "---\ntags: [lifelog]\ntitle: t\n---\n\n## 한눈에\n\n## 요약\n\n- (요약 없음)\n\n"
        "## 전문\n\n> [!quote]- 전문 펼치기 (2 발화)\n> [00:00 · 화자1] 첫 발화.\n"
        "> [00:05 · 화자2] 둘째 발화.\n\n---\n\n출처: PLAUD 녹음 x"
    )
    assert summary_preview(body).splitlines() == [
        "[00:00 · 화자1] 첫 발화.", "[00:05 · 화자2] 둘째 발화.",
    ]


def test_card_label_no_longer_promises_summary_lines_only() -> None:
    content = render_plaud_approval(_record(), preview="- 녹음:: x")
    assert "내용 미리보기(상위 5줄)" in content
    assert "요약 상위" not in content
