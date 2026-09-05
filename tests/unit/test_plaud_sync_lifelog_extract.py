from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation.plaud_sync.lifelog_extract import build_prompt, extract, parse_extraction
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


def _prepare_repo(tmp_path: Path, *, rules: bool = True, template: bool = True) -> Path:
    root = tmp_path / "repo"
    (root / "configs").mkdir(parents=True)
    (root / "prompts").mkdir(parents=True)
    if rules:
        source = (_REPO_ROOT / "configs" / "sensitivity-rules.yaml").read_text(encoding="utf-8")
        (root / "configs" / "sensitivity-rules.yaml").write_text(source, encoding="utf-8")
    if template:
        (root / "prompts" / "lifelog-extraction-v1.md").write_text(_TEMPLATE, encoding="utf-8")
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
    asset = (_REPO_ROOT / "prompts" / "lifelog-extraction-v1.md").read_text(encoding="utf-8")

    # Then
    assert "{{SUMMARY}}" in asset
    assert "{{TRANSCRIPT}}" in asset
    for key in ("people", "places", "decisions", "todos"):
        assert key in asset


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
