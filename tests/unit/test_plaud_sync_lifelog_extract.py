from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation.plaud_sync.lifelog_extract import (
    build_prompt,
    extract,
    parse_extraction,
    summarize,
)
from automation.plaud_sync.lifelog_extract_live import build_extractor
from automation.plaud_sync.lifelog_model import (
    ExtractionSkipped,
    LifelogDecision,
    LifelogExtraction,
    LifelogExtractError,
    LifelogRecording,
    LifelogTodo,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = "요약:\n{{SUMMARY}}\n전문:\n{{TRANSCRIPT}}\n"
_SUMMARY_TEMPLATE = "요약만 만들어라:\n{{TRANSCRIPT}}\n"
_TRANSCRIPT = "[00:12 · 화자1] 내일 세시에 카페에서 만나기로 했습니다."


def _recording(
    *, summary: str = "주간 회의 요약", transcript: str = _TRANSCRIPT
) -> LifelogRecording:
    return LifelogRecording(
        id="rec-1",
        name="주간 회의",
        created_at="2026-09-02T10:00:00+09:00",
        start_at="2026-09-02T09:00:00+09:00",
        duration_ms=60_000,
        summary_markdown=summary,
        transcript_text=transcript,
    )


def _payload(**overrides: object) -> str:
    body: dict[str, object] = {
        "people": ["김철수"],
        "places": ["카페"],
        "decisions": [{"text": "출시일을 금요일로 정함", "at": "01:02"}],
        "todos": [{"text": "보고서 작성", "owner": "김철수", "due": "금요일", "at": "2:03"}],
    }
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False)


def _prepare_repo(
    tmp_path: Path,
    *,
    rules: bool = True,
    template: bool = True,
    summary_template: bool = True,
) -> Path:
    root = tmp_path / "repo"
    (root / "configs").mkdir(parents=True)
    (root / "prompts").mkdir(parents=True)
    if rules:
        source = (_REPO_ROOT / "configs" / "sensitivity-rules.yaml").read_text(encoding="utf-8")
        (root / "configs" / "sensitivity-rules.yaml").write_text(source, encoding="utf-8")
    if template:
        (root / "prompts" / "lifelog-extraction-v4.md").write_text(_TEMPLATE, encoding="utf-8")
    if summary_template:
        (root / "prompts" / "lifelog-summary-v1.md").write_text(_SUMMARY_TEMPLATE, encoding="utf-8")
    return root


def _never_called(prompt: str) -> str:
    raise AssertionError(f"LLM must not be called (prompt length {len(prompt)})")


def test_build_prompt_substitutes_both_placeholders() -> None:
    # When
    prompt = build_prompt(_TEMPLATE, summary="요약 본문", transcript=_TRANSCRIPT)

    # Then
    assert "{{SUMMARY}}" not in prompt
    assert "{{TRANSCRIPT}}" not in prompt
    assert "요약 본문" in prompt
    assert _TRANSCRIPT in prompt


def test_shipped_prompt_asset_carries_the_placeholders_and_keys() -> None:
    # Given
    asset = (_REPO_ROOT / "prompts" / "lifelog-extraction-v4.md").read_text(encoding="utf-8")

    # Then
    assert "{{SUMMARY}}" in asset
    assert "{{TRANSCRIPT}}" in asset
    for key in ("people", "places", "decisions", "todos", "summary"):
        assert key in asset


@pytest.mark.parametrize("value,expected", [("- 첫 항목\n- 둘째 항목", "- 첫 항목\n- 둘째 항목"), ("  ", ""), (None, ""), (12, ""), ([], "")])
def test_summary_preserves_markdown_when_extraction_is_parsed(value: str | int | list[str] | None, expected: str) -> None:
    # Given
    raw = json.dumps({"summary": value})
    # When
    outcome = parse_extraction(raw)
    # Then
    assert outcome.summary == expected


def test_parse_extraction_reads_fenced_json() -> None:
    # Given
    raw = f"```json\n{_payload()}\n```"

    # When
    extraction = parse_extraction(raw)

    # Then
    assert extraction == LifelogExtraction(
        people=("김철수",),
        places=("카페",),
        decisions=(LifelogDecision(text="출시일을 금요일로 정함", at="01:02"),),
        todos=(LifelogTodo(text="보고서 작성", owner="김철수", due="금요일", at="2:03"),),
    )


def test_parse_extraction_ignores_prose_around_the_object() -> None:
    # Given
    raw = f"물론입니다. 아래가 결과입니다.\n{_payload()}\n필요하면 더 알려주세요."

    # When
    extraction = parse_extraction(raw)

    # Then
    assert extraction.people == ("김철수",)
    assert extraction.todos[0].owner == "김철수"


def test_parse_extraction_treats_missing_keys_as_empty() -> None:
    # When
    extraction = parse_extraction('{"people": ["박영희"]}')

    # Then
    assert extraction.people == ("박영희",)
    assert extraction.places == ()
    assert extraction.decisions == ()
    assert extraction.todos == ()


def test_parse_extraction_drops_values_of_the_wrong_shape() -> None:
    # Given: people is not a list, places holds a number, todos holds a textless object
    raw = json.dumps(
        {"people": "박영희", "places": [7, "회의실"], "decisions": {}, "todos": [{"owner": "나"}]},
        ensure_ascii=False,
    )

    # When
    extraction = parse_extraction(raw)

    # Then
    assert extraction.people == ()
    assert extraction.places == ("회의실",)
    assert extraction.decisions == ()
    assert extraction.todos == ()


def test_parse_extraction_keeps_only_well_formed_timestamps() -> None:
    # Given
    raw = json.dumps(
        {
            "decisions": [
                {"text": "가", "at": "1:02"},
                {"text": "나", "at": "01:02:03"},
                {"text": "다", "at": "나중에"},
                {"text": "라", "at": 12},
            ]
        },
        ensure_ascii=False,
    )

    # When
    extraction = parse_extraction(raw)

    # Then
    assert [decision.at for decision in extraction.decisions] == ["1:02", "01:02:03", "", ""]


def test_parse_extraction_caps_list_length_and_clips_long_text() -> None:
    # Given
    raw = json.dumps(
        {
            "people": [f"사람{index}" for index in range(25)],
            "places": [f"장소{index}" for index in range(25)],
            "decisions": [{"text": "가" * 300}] + [{"text": f"결정{i}"} for i in range(25)],
            "todos": [{"text": f"할일{index}"} for index in range(25)],
        },
        ensure_ascii=False,
    )

    # When
    extraction = parse_extraction(raw)

    # Then
    assert len(extraction.people) == 20
    assert len(extraction.places) == 20
    assert len(extraction.decisions) == 20
    assert len(extraction.todos) == 20
    assert extraction.people[0] == "사람0"
    assert len(extraction.decisions[0].text) == 200
    assert extraction.decisions[0].text.endswith("…")


def test_parse_extraction_dedupes_and_normalizes_whitespace() -> None:
    # Given
    raw = json.dumps(
        {"people": ["김  철수", " 김 철수 ", "김\n철수", "박영희", "  "], "places": ["카페", "카페"]},
        ensure_ascii=False,
    )

    # When
    extraction = parse_extraction(raw)

    # Then
    assert extraction.people == ("김 철수", "박영희")
    assert extraction.places == ("카페",)


@pytest.mark.parametrize(
    "raw",
    ["죄송합니다. 추출할 수 없습니다.", "", "{people: 없음", "[1, 2, 3]"],
)
def test_parse_extraction_raises_when_no_object_parses(raw: str) -> None:
    with pytest.raises(LifelogExtractError):
        parse_extraction(raw)


def test_extract_sends_the_filled_prompt_and_returns_the_parsed_fields() -> None:
    # Given
    seen: list[str] = []

    def complete(prompt: str) -> str:
        seen.append(prompt)
        return _payload()

    # When
    extraction = extract(_recording(), template=_TEMPLATE, complete=complete)

    # Then
    assert len(seen) == 1
    assert "주간 회의 요약" in seen[0]
    assert _TRANSCRIPT in seen[0]
    assert extraction.places == ("카페",)


def test_extract_wraps_a_transport_failure_in_lifelog_extract_error() -> None:
    # Given
    def complete(prompt: str) -> str:
        raise TimeoutError("gateway down")

    # When / Then
    with pytest.raises(LifelogExtractError):
        extract(_recording(), template=_TEMPLATE, complete=complete)


def test_build_extractor_skips_patent_sensitive_recordings_without_calling_the_llm(
    tmp_path: Path,
) -> None:
    # Given
    root = _prepare_repo(tmp_path)
    extractor = build_extractor({}, repo_root=root, complete=_never_called)

    # When
    outcome = extractor(_recording(summary="특허 출원 일정 회의"))

    # Then
    assert outcome == ExtractionSkipped("민감도 게이트")


def test_build_extractor_skips_when_the_rules_file_is_absent(tmp_path: Path) -> None:
    # Given
    root = _prepare_repo(tmp_path, rules=False)
    extractor = build_extractor({}, repo_root=root, complete=_never_called)

    # When
    outcome = extractor(_recording())

    # Then
    assert outcome == ExtractionSkipped("민감도 규칙 없음")


def test_build_extractor_skips_when_codex_oauth_is_unavailable(tmp_path: Path) -> None:
    # Given: no injected completer and an environment with no reachable Codex OAuth tier
    root = _prepare_repo(tmp_path)
    extractor = build_extractor({}, repo_root=root)

    # When
    outcome = extractor(_recording())

    # Then: the note keeps its deterministic fields and the 한눈에 line carries the reason
    assert outcome == ExtractionSkipped("LLM 미설정")


def test_build_extractor_live_path_calls_codex_oauth_with_the_user_config_ignored(
    tmp_path: Path,
) -> None:
    # Given: a hermes stand-in that answers only for the measured Codex OAuth argv
    root = _prepare_repo(tmp_path)
    binary = tmp_path / "hermes"
    _ = binary.write_text(
        "#!/bin/sh\n"
        'case " $* " in *" --ignore-user-config "*) ;; *) exit 8 ;; esac\n'
        'case " $* " in *" --provider openai-codex "*) ;; *) exit 7 ;; esac\n'
        f"cat <<'JSON'\n{_payload()}\nJSON\n",
        encoding="utf-8",
    )
    _ = binary.chmod(0o755)
    environment = {"HOME": str(tmp_path), "AUTOPHAGY_HERMES_BIN": str(binary)}
    extractor = build_extractor(environment, repo_root=root)

    # When
    outcome = extractor(_recording())

    # Then
    assert isinstance(outcome, LifelogExtraction)
    assert outcome.people == ("김철수",)


def test_build_extractor_fails_the_poll_when_codex_oauth_refuses(tmp_path: Path) -> None:
    # Given: the measured fail-closed signal from a home with no Codex credentials
    root = _prepare_repo(tmp_path)
    binary = tmp_path / "hermes"
    _ = binary.write_text(
        "#!/bin/sh\n>&2 printf 'agent failed: No Codex credentials stored\\n'\nexit 1\n",
        encoding="utf-8",
    )
    _ = binary.chmod(0o755)
    environment = {"HOME": str(tmp_path), "AUTOPHAGY_HERMES_BIN": str(binary)}
    extractor = build_extractor(environment, repo_root=root)

    # When / Then: this poll fails and retries later; no other provider is attempted
    with pytest.raises(LifelogExtractError):
        extractor(_recording())


def test_build_extractor_raises_when_the_template_is_missing(tmp_path: Path) -> None:
    # Given
    root = _prepare_repo(tmp_path, template=False)
    extractor = build_extractor({}, repo_root=root, complete=_never_called)

    # When / Then
    with pytest.raises(LifelogExtractError):
        extractor(_recording())


def test_build_extractor_returns_the_parsed_extraction_on_the_happy_path(tmp_path: Path) -> None:
    # Given
    root = _prepare_repo(tmp_path)
    seen: list[str] = []

    def complete(prompt: str) -> str:
        seen.append(prompt)
        return f"```json\n{_payload()}\n```"

    extractor = build_extractor({}, repo_root=root, complete=complete)

    # When
    outcome = extractor(_recording())

    # Then
    assert outcome == LifelogExtraction(
        people=("김철수",),
        places=("카페",),
        decisions=(LifelogDecision(text="출시일을 금요일로 정함", at="01:02"),),
        todos=(LifelogTodo(text="보고서 작성", owner="김철수", due="금요일", at="2:03"),),
    )
    assert _TRANSCRIPT in seen[0]


def test_build_extractor_honors_the_prompt_path_override(tmp_path: Path) -> None:
    # Given
    root = _prepare_repo(tmp_path, template=False)
    override = tmp_path / "custom-prompt.md"
    override.write_text("맞춤 지시\n{{SUMMARY}}\n{{TRANSCRIPT}}\n", encoding="utf-8")
    environment = {"PLAUD_SYNC_EXTRACT_PROMPT": str(override)}
    seen: list[str] = []

    def complete(prompt: str) -> str:
        seen.append(prompt)
        return _payload()

    extractor = build_extractor(environment, repo_root=root, complete=complete)

    # When
    outcome = extractor(_recording())

    # Then
    assert isinstance(outcome, LifelogExtraction)
    assert seen[0].startswith("맞춤 지시")


# --- 요약 누락 차단 (2026-09-06 소유자 지시) --------------------------------


def test_summary_survives_a_bullet_array_from_the_model() -> None:
    """프롬프트가 '마크다운 불릿 3–6개'를 요구하므로 모델은 배열로 답할 수 있다.

    실측 2026-09-04 노트: 사람·장소·결정·할 일은 채워졌는데 '## 요약'만 비었다.
    문자열일 때만 요약을 받으면 그 모양의 응답이 통째로 사라진다.
    """
    # Given
    raw = json.dumps(
        {"summary": ["첫 결론", "- 둘째 결론", {"text": "셋째 결론"}, "  ", 12]},
        ensure_ascii=False,
    )

    # When
    outcome = parse_extraction(raw)

    # Then
    assert outcome.summary == "- 첫 결론\n- 둘째 결론\n- 셋째 결론"


def test_build_extractor_repairs_an_empty_summary_with_one_more_call(tmp_path: Path) -> None:
    """Plaud 요약도 추출 요약도 없으면 요약만 다시 묻는다 — 빈 요약으로 노트를 얼리지 않는다."""
    # Given
    root = _prepare_repo(tmp_path)
    prompts: list[str] = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        if len(prompts) == 1:
            return _payload()  # summary 키가 없는 응답
        return json.dumps({"summary": "- 다시 받은 요약"}, ensure_ascii=False)

    extractor = build_extractor({}, repo_root=root, complete=complete)

    # When
    outcome = extractor(_recording(summary=""))

    # Then
    assert isinstance(outcome, LifelogExtraction)
    assert outcome.summary == "- 다시 받은 요약"
    assert outcome.people == ("김철수",)
    assert len(prompts) == 2
    assert _TRANSCRIPT in prompts[1]


def test_build_extractor_does_not_retry_when_plaud_already_summarized(tmp_path: Path) -> None:
    """노트가 Plaud 요약을 쓰는 경우에는 추가 호출이 없다 — 복구는 정말 빈 경우만이다."""
    # Given
    root = _prepare_repo(tmp_path)
    prompts: list[str] = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        return _payload()

    extractor = build_extractor({}, repo_root=root, complete=complete)

    # When
    outcome = extractor(_recording(summary="Plaud 가 준 요약"))

    # Then
    assert isinstance(outcome, LifelogExtraction)
    assert len(prompts) == 1


def test_build_extractor_keeps_the_fields_when_the_summary_retry_fails(tmp_path: Path) -> None:
    """복구 호출이 실패해도 이미 얻은 사람·장소·결정을 버리지 않는다.

    추출은 성공했다. 요약 재시도의 실패로 이번 폴 전체를 실패시키면 그 좋은 결과가
    사라지고 노트는 다음 폴까지 존재하지 않는다 — 사유를 적고 나머지를 살린다.
    """
    # Given
    root = _prepare_repo(tmp_path)
    calls: list[str] = []

    def complete(prompt: str) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return _payload()
        raise TimeoutError("gateway down")

    extractor = build_extractor({}, repo_root=root, complete=complete)

    # When
    outcome = extractor(_recording(summary=""))

    # Then
    assert isinstance(outcome, LifelogExtraction)
    assert outcome.people == ("김철수",)
    assert outcome.summary == ""
    assert len(calls) == 2


def test_shipped_summary_repair_asset_carries_its_placeholder_and_key() -> None:
    """복구 프롬프트는 배포 자산이다 — 없으면 요약 복구가 조용히 꺼진다."""
    # Given / When
    asset = (_REPO_ROOT / "prompts" / "lifelog-summary-v1.md").read_text(encoding="utf-8")

    # Then
    assert "<<<PROMPT>>>" in asset
    assert "{{TRANSCRIPT}}" in asset
    assert "summary" in asset


def test_shipped_summary_repair_prompt_carries_its_placeholders() -> None:
    """복구 프롬프트는 배포 자산이다 — 없으면 요약 복구가 조용히 무력해진다."""
    asset = (_REPO_ROOT / "prompts" / "lifelog-summary-v1.md").read_text(encoding="utf-8")

    assert "<<<PROMPT>>>" in asset
    assert "{{TRANSCRIPT}}" in asset
    assert "summary" in asset


# --- 요약 복구의 경계 (2026-09-06 감사) --------------------------------------


def test_summarize_refuses_prose_that_is_not_a_summary() -> None:
    """JSON 도 불릿도 아닌 산문은 요약이 아니다.

    복구 호출은 '요약이 하나도 없다' 는 상태에서만 도달한다. 거기서 거절문("요약할 수
    없습니다")을 요약으로 받아들이면 빈 요약보다 나쁘다 — 노트가 내용이 있는 척한다.
    """
    # Given
    def complete(prompt: str) -> str:
        return "죄송합니다. 제공된 전사본만으로는 요약을 만들 수 없습니다."

    # When / Then
    assert summarize(_recording(summary=""), template=_TEMPLATE, complete=complete) == ""


def test_summarize_accepts_a_bullet_list_that_is_not_json() -> None:
    """펜스도 JSON 도 없이 불릿만 답하는 모델이 있다 — 그것은 요약이 맞다."""
    # Given
    def complete(prompt: str) -> str:
        return "- 첫 결론\n- 둘째 결론"

    # When / Then
    assert summarize(_recording(summary=""), template=_TEMPLATE, complete=complete) == (
        "- 첫 결론\n- 둘째 결론"
    )


def test_summary_array_is_capped_like_the_other_list_fields() -> None:
    """배열 요약만 상한이 없으면 모델 한 번의 폭주가 노트 본문으로 그대로 들어간다."""
    # Given
    raw = json.dumps({"summary": [f"항목 {index}" for index in range(30)]}, ensure_ascii=False)

    # When
    lines = parse_extraction(raw).summary.splitlines()

    # Then
    assert len(lines) == 20
    assert lines[0] == "- 항목 0"


def test_summary_array_items_are_clipped_to_the_text_limit() -> None:
    """불릿 하나가 200자를 넘기면 다른 모든 필드와 같은 자리에서 잘린다."""
    # Given
    raw = json.dumps({"summary": ["가" * 400]}, ensure_ascii=False)

    # When
    summary = parse_extraction(raw).summary

    # Then
    assert summary.startswith("- ")
    assert summary.endswith("…")
    assert len(summary) == 202


def test_parse_extraction_reads_the_generated_title() -> None:
    """Plaud 가 제목을 못 붙인 녹음의 이름 자리를 채울 제목을 같은 응답에서 받는다.

    추가 호출은 없다 — 요약·사람·장소를 받는 그 호출에 키 하나가 늘 뿐이다.
    제목이 없거나 문자열이 아니면 빈 문자열이고, 그때는 Plaud 이름이 그대로 이름이다.
    """
    raw = (
        '{"people": [], "places": [], "decisions": [], "todos": [],'
        ' "summary": "- 한 줄", "title": "직장 동료들의 일상 대화"}'
    )

    assert parse_extraction(raw).title == "직장 동료들의 일상 대화"
    assert parse_extraction('{"summary": ""}').title == ""
    assert parse_extraction('{"title": 7}').title == ""
