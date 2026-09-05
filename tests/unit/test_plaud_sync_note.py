from __future__ import annotations

import re
from dataclasses import replace

import pytest

from zoneinfo import ZoneInfo

from automation import term_correction
from automation.plaud_sync.lifelog_model import (
    ExtractionSkipped,
    LifelogDecision,
    LifelogExtraction,
    LifelogTodo,
)
from automation.plaud_sync.note import (
    LifelogRecording,
    PlaudNoteError,
    plan_lifelog_note,
    render_lifelog_body,
    split_lifelog_body,
)

_SKIPPED = ExtractionSkipped("테스트")


def test_render_lifelog_body_places_summary_before_transcript() -> None:
    # Given
    recording = LifelogRecording(
        id="recording-1",
        name="주간 회의",
        created_at="2026-09-02T10:00:00+09:00",
        start_at="2026-09-02T09:00:00+09:00",
        duration_ms=59_000,
        summary_markdown="- 결정: 출시",
        transcript_text="참석자: 출시를 결정했습니다.",
    )

    # When
    body = render_lifelog_body(recording, extraction=_SKIPPED)

    # Then
    sections = [line for line in body.splitlines() if line.startswith("## ")]
    assert sections == ["## 한눈에", "## 요약", "## 전문"]
    assert body.index("## 요약") < body.index("## 전문")
    assert "- 결정: 출시" in body
    assert "참석자: 출시를 결정했습니다." in body
    assert body.startswith("---\ntags: [lifelog]\n")
    assert not body.endswith("\n")


def test_plan_lifelog_note_uses_a_stable_id_digest_path() -> None:
    # Given
    first_recording = LifelogRecording(
        id="recording-1",
        name="일일 기록",
        created_at="2026-09-02T10:00:00+09:00",
        start_at="2026-09-02T09:00:00+09:00",
        duration_ms=60_000,
        summary_markdown="요약",
        transcript_text="전문",
    )
    second_recording = replace(first_recording, id="recording-2")

    # When
    first = plan_lifelog_note(first_recording, extraction=_SKIPPED)
    repeated = plan_lifelog_note(first_recording, extraction=_SKIPPED)
    second = plan_lifelog_note(second_recording, extraction=_SKIPPED)

    # Then
    assert first.relpath == repeated.relpath
    assert first.relpath.parent.parts == ("000_PARA", "Area", "Lifelog", "2026")
    assert first.relpath.suffix == ".md"
    assert first.relpath.name != second.relpath.name
    assert re.fullmatch(r"2026-09-02-.+--[0-9a-f]{12}\.md", first.relpath.name)


@pytest.mark.parametrize(
    ("name", "expected_slug"),
    [
        ("2026-09-02", "recording"),
        ("2026-09-02 meeting", "meeting"),
        ("20260902T130522", "130522"),
        ("2026_09_02-13-05-22", "13-05-22"),
        ("meeting", "meeting"),
    ],
)
def test_plan_lifelog_note_strips_recording_date_from_filename(
    name: str, expected_slug: str
) -> None:
    recording = LifelogRecording(
        id="date-prefixed",
        name=name,
        created_at="2026-09-02T10:00:00+09:00",
        start_at="2026-09-02T09:00:00+09:00",
        duration_ms=0,
        summary_markdown="",
        transcript_text="",
    )

    plan = plan_lifelog_note(recording, extraction=_SKIPPED)

    assert plan.relpath.name.startswith(f"2026-09-02-{expected_slug}--")
    assert plan.title == f"{name} (2026-09-02)"


def test_plan_lifelog_note_falls_back_to_created_at_and_rejects_bad_dates() -> None:
    # Given
    fallback_recording = LifelogRecording(
        id="fallback",
        name="기록",
        created_at="2026-09-03T10:00:00+09:00",
        start_at="not-a-date",
        duration_ms=0,
        summary_markdown="",
        transcript_text="",
    )
    malformed_recording = LifelogRecording(
        id="malformed",
        name="기록",
        created_at="also-not-a-date",
        start_at="not-a-date",
        duration_ms=0,
        summary_markdown="",
        transcript_text="",
    )

    # When
    fallback_plan = plan_lifelog_note(fallback_recording, extraction=_SKIPPED)

    # Then
    assert fallback_plan.relpath.parent.name == "2026"
    assert fallback_plan.relpath.name.startswith("2026-09-03-")
    with pytest.raises(PlaudNoteError):
        _ = plan_lifelog_note(malformed_recording, extraction=_SKIPPED)


def test_plan_lifelog_note_uses_empty_content_and_name_fallbacks() -> None:
    # Given
    recording = LifelogRecording(
        id="empty",
        name="   ",
        created_at="2026-09-02T10:00:00+09:00",
        start_at="",
        duration_ms=0,
        summary_markdown=" \n\t ",
        transcript_text="\n",
    )

    # When
    plan = plan_lifelog_note(recording, extraction=_SKIPPED)

    # Then
    assert plan.title == "녹음 (2026-09-02)"
    assert plan.relpath.name.startswith("2026-09-02-recording--")
    assert "- (요약 없음)" in plan.body
    assert "- (전사 없음)" in plan.body


def test_plan_lifelog_note_makes_korean_punctuation_names_filename_safe() -> None:
    # Given
    recording = LifelogRecording(
        id="safe-name",
        name="주간 회의: 9/2 (화)",
        created_at="2026-09-02T10:00:00+09:00",
        start_at="",
        duration_ms=0,
        summary_markdown="",
        transcript_text="",
    )
    long_name_recording = LifelogRecording(
        id="long-name",
        name="a" * 90,
        created_at="2026-09-02T10:00:00+09:00",
        start_at="",
        duration_ms=0,
        summary_markdown="",
        transcript_text="",
    )

    # When
    safe_plan = plan_lifelog_note(recording, extraction=_SKIPPED)
    long_plan = plan_lifelog_note(long_name_recording, extraction=_SKIPPED)

    # Then
    assert all(character not in safe_plan.relpath.name for character in ':\\/()')
    assert safe_plan.relpath.name.startswith("2026-09-02-")
    long_slug = long_plan.relpath.name.removeprefix("2026-09-02-").split("--", 1)[0]
    assert len(long_slug) <= 60


@pytest.mark.parametrize(
    ("duration_ms", "duration"),
    [
        (0, "0초"),
        (59_000, "59초"),
        (60_000, "1분 0초"),
        (3_661_000, "61분 1초"),
    ],
)
def test_render_lifelog_body_renders_duration_in_korean_style(
    duration_ms: int, duration: str
) -> None:
    # Given
    recording = LifelogRecording(
        id="duration",
        name="기록",
        created_at="2026-09-02T10:00:00+09:00",
        start_at="",
        duration_ms=duration_ms,
        summary_markdown="",
        transcript_text="",
    )

    # When
    body = render_lifelog_body(recording, extraction=_SKIPPED)

    # Then
    source_line = body.splitlines()[-1]
    assert source_line == f"출처: PLAUD 녹음 duration · 2026-09-02T10:00:00+09:00 · {duration}"


def test_split_lifelog_body_reads_back_a_summary_that_carries_its_own_headings() -> None:
    from automation.plaud_sync.note import render_lifelog_body, split_lifelog_body

    recording = LifelogRecording(
        id="rec-split",
        name="점심",
        created_at="2026-09-02T02:19:04Z",
        start_at="2026-09-02T02:19:04Z",
        duration_ms=1000,
        summary_markdown="개요 문장.\n\n------------\n## 소제목\n- 항목",
        transcript_text="[00:00 · Speaker 1] 안녕\n\n---\n\n[00:05 · Speaker 2] 네",
    )
    summary, transcript = split_lifelog_body(render_lifelog_body(recording, extraction=_SKIPPED))
    assert summary == recording.summary_markdown
    assert transcript == recording.transcript_text


def test_render_lifelog_body_drops_plaud_poster_images_the_vault_cannot_resolve() -> None:
    # 2026-09-04 실측: Plaud 요약이 자기 스토리지 키(permanent/<uid>/<file>/summary_poster/card_*.png)를
    # 가리키는 포스터 이미지로 시작해 Obsidian 이 "…png 을 찾지 못했습니다" 를 띄웠다. 상대 경로 이미지는
    # vault 에 존재할 수 없으므로 버리고, 절대 URL 이미지는 그대로 둔다.
    recording = LifelogRecording(
        id="poster",
        name="직장 동료들의 일상 대화",
        created_at="2026-09-02T10:00:00+09:00",
        start_at="2026-09-02T09:02:00+09:00",
        duration_ms=60_000,
        summary_markdown=(
            "![PLAUD NOTE](permanent/f768/mem_abc/summary_poster/card_20260902-v2@5045_ebe4.png)\n\n"
            "## 개요\n직장 동료들이 식사를 함께한다.\n\n"
            "![외부 그림](https://example.com/chart.png)\n"
        ),
        transcript_text="[00:00 · 화자1] 안녕하세요.",
    )

    body = render_lifelog_body(recording, extraction=_SKIPPED)

    assert "summary_poster" not in body
    assert "![PLAUD NOTE]" not in body
    assert "![외부 그림](https://example.com/chart.png)" in body
    assert "\n## 요약\n\n## 개요\n직장 동료들이 식사를 함께한다.\n\n![외부 그림]" in body


def test_render_lifelog_body_leaves_a_summary_without_images_untouched() -> None:
    recording = LifelogRecording(
        id="plain",
        name="기록",
        created_at="2026-09-02T10:00:00+09:00",
        start_at="2026-09-02T09:00:00+09:00",
        duration_ms=0,
        summary_markdown="## 개요\n첫 문단.\n\n- 항목 [링크](https://example.com)\n",
        transcript_text="",
    )

    body = render_lifelog_body(recording, extraction=_SKIPPED)

    assert "\n## 요약\n\n## 개요\n첫 문단.\n\n- 항목 [링크](https://example.com)\n\n## 전문" in body


# ---- v2 양식 (B안, 2026-09-04 소유자 결정): frontmatter → 한눈에 → 요약 → 결정 · 할 일 → 접힌 전문 ----

_SEOUL = ZoneInfo("Asia/Seoul")
_EXTRACTION = LifelogExtraction(
    people=("김철수", "박영희"),
    places=("구내식당",),
    decisions=(LifelogDecision("다음 주 세미나 참석", at="12:40"),),
    todos=(
        LifelogTodo("세미나 일정 확인", owner="나", due="다음 주", at="12:41"),
        LifelogTodo("자료 공유"),
    ),
)


def _real_shape_recording() -> LifelogRecording:
    # 2026-09-02 실측 모양: 포스터 이미지로 시작, 자체 구분선·소제목, UTC 타임스탬프.
    return LifelogRecording(
        id="mem_clRcZZ53qx",
        name="2026-09-02 09:02 직장 동료들의 일상 대화: 업무, 진로, 취미",
        created_at="2026-09-02T00:30:00+00:00",
        start_at="2026-09-02T00:02:00+00:00",
        duration_ms=1_830_000,
        summary_markdown=(
            "![PLAUD NOTE](permanent/x/summary_poster/card.png)\n\n"
            "직장 동료들이 점심을 함께하며 업무와 진로를 이야기한다. 취미도 나눈다.\n\n"
            "------------\n## 일상 잡담\n- 첫 항목.\n\n## 업무\n- 둘째 항목.\n\n## 결론\n- 셋째.\n"
        ),
        transcript_text=(
            "[00:00 · 화자1] 안녕하세요.\n[00:05 · 화자2] 네.\n[00:09 · 화자1] 점심 먹죠."
        ),
    )


_V2_GOLDEN = (
    "---\n"
    "tags: [lifelog, lifelog/일상-잡담, lifelog/업무]\n"
    'title: "2026-09-02 09:02 직장 동료들의 일상 대화: 업무, 진로, 취미 (2026-09-02)"\n'
    "source: PLAUD 녹음 mem_clRcZZ53qx\n"
    "created: 2026-09-02T09:02:00\n"
    "modified: 2026-09-02T09:02:00\n"
    "---\n"
    "\n"
    "## 한눈에\n"
    "\n"
    "- 녹음:: 2026-09-02 (수) 09:02 · 30분 30초 · 화자 2명\n"
    "- 주제:: #lifelog/일상-잡담 #lifelog/업무\n"
    "- 사람:: [[김철수]], [[박영희]]\n"
    "- 장소:: 구내식당\n"
    "- 한 줄:: 직장 동료들이 점심을 함께하며 업무와 진로를 이야기한다.\n"
    "\n"
    "## 요약\n"
    "\n"
    "직장 동료들이 점심을 함께하며 업무와 진로를 이야기한다. 취미도 나눈다.\n"
    "\n"
    "------------\n"
    "## 일상 잡담\n"
    "- 첫 항목.\n"
    "\n"
    "## 업무\n"
    "- 둘째 항목.\n"
    "\n"
    "## 결론\n"
    "- 셋째.\n"
    "\n"
    "## 결정 · 할 일\n"
    "\n"
    "- 결정: 다음 주 세미나 참석 [12:40]\n"
    "- [ ] 세미나 일정 확인 — 담당 나 · 기한 다음 주 [12:41]\n"
    "- [ ] 자료 공유\n"
    "\n"
    "## 전문\n"
    "\n"
    "> [!quote]- 전문 펼치기 (3 발화)\n"
    "> [00:00 · 화자1] 안녕하세요.\n"
    "> [00:05 · 화자2] 네.\n"
    "> [00:09 · 화자1] 점심 먹죠.\n"
    "\n"
    "---\n"
    "\n"
    "출처: PLAUD 녹음 mem_clRcZZ53qx · 2026-09-02T00:02:00+00:00 · 30분 30초"
)


def test_render_lifelog_body_v2_matches_the_golden_note() -> None:
    body = render_lifelog_body(_real_shape_recording(), extraction=_EXTRACTION, tz=_SEOUL)

    assert body == _V2_GOLDEN


def test_plan_lifelog_note_v2_title_and_path_follow_the_local_date() -> None:
    plan = plan_lifelog_note(_real_shape_recording(), extraction=_EXTRACTION, tz=_SEOUL)

    assert plan.title == "2026-09-02 09:02 직장 동료들의 일상 대화: 업무, 진로, 취미 (2026-09-02)"
    assert plan.relpath.name.startswith("2026-09-02-0902-")
    assert plan.body == _V2_GOLDEN


def test_render_lifelog_body_quotes_the_title_only_when_yaml_needs_it() -> None:
    plain = replace(_real_shape_recording(), name="평범한 제목")
    colon = replace(_real_shape_recording(), name="제목: 부제")

    assert "\ntitle: 평범한 제목 (2026-09-02)\n" in render_lifelog_body(plain, extraction=_SKIPPED, tz=_SEOUL)
    assert '\ntitle: "제목: 부제 (2026-09-02)"\n' in render_lifelog_body(colon, extraction=_SKIPPED, tz=_SEOUL)


def test_render_lifelog_body_marks_a_skipped_extraction_without_people_or_places() -> None:
    body = render_lifelog_body(
        _real_shape_recording(), extraction=ExtractionSkipped("민감도 게이트"), tz=_SEOUL
    )

    assert "- 추출:: 생략 (민감도 게이트)\n" in body
    assert "사람::" not in body
    assert "장소::" not in body
    assert "## 결정 · 할 일" not in body
    assert body.index("- 추출::") < body.index("- 한 줄::")


def test_render_lifelog_body_omits_empty_extraction_lines_and_the_decisions_section() -> None:
    body = render_lifelog_body(_real_shape_recording(), extraction=LifelogExtraction(), tz=_SEOUL)

    assert "사람::" not in body
    assert "장소::" not in body
    assert "추출::" not in body
    assert "## 결정 · 할 일" not in body
    assert "\n## 요약\n" in body and "\n## 전문\n" in body


def test_render_lifelog_body_without_transcript_has_no_callout() -> None:
    body = render_lifelog_body(
        replace(_real_shape_recording(), transcript_text=""), extraction=_SKIPPED, tz=_SEOUL
    )

    assert "## 전문\n\n- (전사 없음)\n" in body
    assert "[!quote]" not in body
    assert "화자" not in body.split("## 요약")[0]


def test_render_lifelog_body_converts_start_at_into_the_note_timezone() -> None:
    late_utc = replace(_real_shape_recording(), start_at="2026-09-01T23:30:00+00:00")

    plan = plan_lifelog_note(late_utc, extraction=_SKIPPED, tz=_SEOUL)

    assert "\ncreated: 2026-09-02T08:30:00\nmodified: 2026-09-02T08:30:00\n" in plan.body
    assert "- 녹음:: 2026-09-02 (수) 08:30 · " in plan.body
    assert plan.relpath.name.startswith("2026-09-02-")
    assert plan.relpath.parent.name == "2026"


def test_split_lifelog_body_reads_back_the_transcript_without_the_quote_prefix() -> None:
    recording = _real_shape_recording()

    summary, transcript = split_lifelog_body(
        render_lifelog_body(recording, extraction=_EXTRACTION, tz=_SEOUL)
    )

    assert transcript == recording.transcript_text
    assert summary.startswith("직장 동료들이 점심을 함께하며")
    assert "결정 · 할 일" not in summary and "[[김철수]]" not in summary


# ---- 용어 교정 (소유자 결정 2026-09-05): 산출 문서에서만 고치고 '## 전문' 은 인식된 그대로 둔다 ----


def test_the_note_body_is_corrected_and_the_transcript_is_not() -> None:
    # 전사본은 증거다. 틀린 교정을 원문에 새기면 원래 낱말이 어디에도 남지 않으므로, 노트가
    # 스스로 쓰는 구역(한눈에·요약·결정·할 일)만 고치고 접힌 '## 전문' 은 손대지 않는다.
    recording = replace(
        _real_shape_recording(),
        summary_markdown="항정기술과 열교환기 사양을 논의했다.",
        transcript_text="[00:00 · 화자1] 항정기술과 열교환기 사양을 논의했다.",
    )

    body = render_lifelog_body(
        recording, extraction=_SKIPPED, tz=_SEOUL, glossary=(("한전기술", "한전기술"),)
    )

    summary, transcript = split_lifelog_body(body)
    assert "한전기술과 열교환기 사양을 논의했다." in summary
    assert "항정기술" not in summary
    assert "항정기술과 열교환기 사양을 논의했다." in transcript
    assert "한전기술" not in transcript


def test_corrected_lifelog_note_fixes_the_extracted_fields_and_reports_every_word() -> None:
    from automation.plaud_sync.note import corrected_lifelog_note

    extraction = LifelogExtraction(
        people=("김철수",),
        places=("항정기술 회의실",),
        decisions=(LifelogDecision("항정기술 방문", at="12:40"),),
        todos=(LifelogTodo("열기환기 점검", owner="항정기술 담당", due="다음 주"),),
    )
    recording = replace(
        _real_shape_recording(),
        summary_markdown="항정기술 방문을 정했다.",
        transcript_text="[00:00 · 화자1] 항정기술 이야기.",
    )

    note = corrected_lifelog_note(
        recording,
        extraction=extraction,
        tz=_SEOUL,
        glossary=(("한전기술", "한전기술"), ("열기환기", "열교환기")),
    )

    assert "- 사람:: [[김철수]]" in note.plan.body
    assert "- 장소:: 한전기술 회의실" in note.plan.body
    assert "- 결정: 한전기술 방문 [12:40]" in note.plan.body
    assert "- [ ] 열교환기 점검 — 담당 한전기술 담당 · 기한 다음 주" in note.plan.body
    assert "> [00:00 · 화자1] 항정기술 이야기." in note.plan.body, "전문은 그대로다"
    assert note.plan.body.splitlines()[-1].startswith("출처: PLAUD 녹음 mem_clRcZZ53qx")
    changed = [(item.before, item.after, item.kind) for item in note.corrections]
    assert ("열기환기", "열교환기", term_correction.EXACT) in changed
    assert changed.count(("항정기술", "한전기술", term_correction.FUZZY)) == 4


def test_the_note_title_is_corrected_while_the_path_keeps_its_identity() -> None:
    """제목은 사람이 읽는 첫 줄이라 고치고, 파일 이름은 그 노트의 신원이라 고정한다.

    Plaud 가 붙이는 녹음 이름도 음성에서 나오므로 같은 오인식을 안고 온다. 그러나 경로는
    슬러그에서 오고, 경로가 참고 문서에 따라 움직이면 용어집을 한 줄 고친 날 같은 녹음이
    노트 둘로 갈라진다.
    """
    from automation.plaud_sync.note import corrected_lifelog_note

    recording = replace(_real_shape_recording(), name="항정기술 미팅")

    fixed = corrected_lifelog_note(
        recording, extraction=_SKIPPED, tz=_SEOUL, glossary=(("한전기술", "한전기술"),)
    )
    plain = corrected_lifelog_note(recording, extraction=_SKIPPED, tz=_SEOUL)

    assert fixed.plan.title.startswith("한전기술 미팅")
    assert "title: 한전기술 미팅" in fixed.plan.body
    assert fixed.plan.relpath == plain.plan.relpath


def test_plan_lifelog_note_without_a_glossary_changes_nothing() -> None:
    recording = replace(_real_shape_recording(), summary_markdown="항정기술과 회의했다.")

    plan = plan_lifelog_note(recording, extraction=_SKIPPED, tz=_SEOUL)

    assert "항정기술과 회의했다." in plan.body


# ---- yaml_scalar: 실제 Linter 빌드(escape char '"')가 title 에 써넣는 값과 대조했다 (docs/qa/PLV2) ----


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('평범한 제목 (2026-09-02)', '평범한 제목 (2026-09-02)'),
        ('회의 (9/2)', '회의 (9/2)'),
        ('제목: 부제', '"제목: 부제"'),
        ('주간 #회의', '"주간 #회의"'),
        ('#해시로 시작', '"#해시로 시작"'),
        ('- 대시로 시작', '"- 대시로 시작"'),
        ('[대괄호] 제목', '"[대괄호] 제목"'),
        ("'작은따옴표' 제목", '"\'작은따옴표\' 제목"'),
        ("중간 '작은' 따옴표", "\"중간 '작은' 따옴표\""),
        ("끝에 따옴표'", "\"끝에 따옴표'\""),
        ('~ 제목', '~ 제목'),
        ('? 제목', '"? 제목"'),
        ('"큰따옴표로 시작" 제목', '\'"큰따옴표로 시작" 제목\''),
        ('"큰따옴표로 시작" 그리고 \'작은\'', '"\\"큰따옴표로 시작\\" 그리고 \'작은\'"'),
        ('따옴표 "안" 제목', '\'따옴표 "안" 제목\''),
        ('혼합 "큰" \'작은\' 제목', '혼합 "큰" \'작은\' 제목'),
        ('콜론: "큰따옴표"', '\'콜론: "큰따옴표"\''),
        ("콜론: '작은따옴표'", '"콜론: \'작은따옴표\'"'),
        ('콜론: 백\\슬래시', '"콜론: 백\\\\슬래시"'),
        ('콜론: 둘 "다" \'있음\'', '"콜론: 둘 \\"다\\" \'있음\'"'),
    ],
)
def test_yaml_scalar_mirrors_the_linters_title_escaping(value: str, expected: str) -> None:
    from automation.plaud_sync.lifelog_fields import yaml_scalar

    assert yaml_scalar(value) == expected
