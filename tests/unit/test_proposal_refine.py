from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skills.proposal.scripts import proposal_cli, proposal_refine
from skills.proposal.scripts.proposal_refine import (
    REFINEMENT_INVARIANT_FAILED_EXIT,
    RefinementInputError,
    RefinementInvariantFailed,
    chunk_markdown,
    mask_immutables,
    refine_section,
    refine_version,
    verify_invariants,
)
from skills.proposal.scripts.proposal_route_guard import RouteRefused
from skills.proposal.scripts.proposal_version import Staging, VersionStore


Transform = Callable[[str, int], str]
_SENTINEL: re.Pattern[str] = re.compile(r"@@IMMUTABLE_[0-9]{4,}@@")


def _identity_transform(text: str, _index: int) -> str:
    return text


class RecordingTransport:
    def __init__(self, transform: Transform | None = None) -> None:
        self.calls: list[tuple[str, str, float]] = []
        self._transform: Transform = transform or _identity_transform

    def __call__(self, text: str, host: str, timeout: float) -> str:
        self.calls.append((text, host, timeout))
        return self._transform(text, len(self.calls))


def _section(body: str, section_id: str = "0", title: str = "연구 개요") -> dict[str, object]:
    return {
        "body": body,
        "claims": [{"source_ids": [f"C{int(section_id) + 1:02d}"], "text": "근거 주장"}],
        "section_id": section_id,
        "title": title,
    }


def _version(root: Path, sections: list[dict[str, object]]) -> Path:
    store = VersionStore(root)
    run_key = hashlib.sha256(str(root).encode()).hexdigest()
    staging = store.begin("demo", run_key)
    assert isinstance(staging, Staging)
    version = store.promote(
        "demo",
        staging,
        {"parent": None, "request": {"profile": "30-page"}, "schema_version": 1},
    )
    version_path = root / "demo" / "versions" / version
    drafts = version_path / "out" / "drafts.json"
    _ = drafts.write_text(
        json.dumps({"sections": sections}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    drafts.chmod(0o600)
    return version_path


def _configure(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    monkeypatch.setenv("PROPOSAL_REFINE_TIMEOUT_SECONDS", "4.5")


def _json_object(path: Path) -> dict[str, object]:
    value = cast(object, json.loads(path.read_text(encoding="utf-8")))
    assert isinstance(value, dict)
    mapping = cast(dict[object, object], value)
    assert all(isinstance(key, str) for key in mapping)
    return cast(dict[str, object], mapping)


def _object_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    mapping = cast(dict[object, object], value)
    assert all(isinstance(key, str) for key in mapping)
    return cast(dict[str, object], mapping)


def _report(version: Path) -> dict[str, object]:
    return _json_object(version / "out" / "refine-report.json")


def _reported_chunk(version: Path, section_index: int = 0) -> dict[str, object]:
    sections = _object_list(_report(version)["sections"])
    section = _mapping(sections[section_index])
    chunks = _object_list(section["chunks"])
    return _mapping(chunks[0])


def _replace_token(text: str, index: int, replacement: str) -> str:
    tokens = [match.group(0) for match in _SENTINEL.finditer(text)]
    assert len(tokens) > index
    return text.replace(tokens[index], replacement, 1)


def test_changed_number_rejects_chunk_and_keeps_original(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = "# 연구 목표\n\nKIMM은 굴착 오차를 10 mm 이하로 관리한다.\n"
    second = "# 수행 방법\n\n검증 절차를 반복한다.\n"
    version = _version(tmp_path, [_section(first), _section(second, "1", "수행 방법")])

    def corrupt_number(text: str, call_index: int) -> str:
        if call_index != 1:
            return text
        token_count = sum(1 for _match in _SENTINEL.finditer(text))
        return _replace_token(text, token_count - 1, "11 mm")

    transport = RecordingTransport(corrupt_number)
    _configure(monkeypatch, tmp_path)

    result = refine_version("demo", transport=transport, host="codex-oauth")

    assert result.failed_chunks == 1
    assert result.refined is False
    assert result.reason == "no-content-changed"
    assert not result.output_path.exists()
    chunk = _reported_chunk(version)
    assert chunk["passed"] is False
    failed = _object_list(chunk["failed_invariants"])
    assert "numbers-units" in failed


def test_korean_counter_change_rejects_chunk() -> None:
    body = "# 연구 목표\n\n자료 30장을 검증한다.\n"
    transport = RecordingTransport(
        lambda text, _index: _replace_token(text, 1, "30부")
    )

    with pytest.raises(RefinementInvariantFailed) as raised:
        _ = refine_section(body, transport, host="codex-oauth")

    result = raised.value.result
    assert result is not None
    assert result.text == body
    assert "numbers-units" in result.chunks[0].failed_invariants


def test_korean_counter_round_trips_losslessly_with_identity_transport() -> None:
    body = "# 연구 목표\n\n자료 30장을 검증한다.\n"

    result = refine_section(body, RecordingTransport(), host="codex-oauth")

    assert result.text == body
    assert result.chunks[0].passed is True


def test_missing_figure_token_rejects_only_that_chunk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = "# 연구 목표\n\n[[FIG:fig-s1-01]]의 결과를 검증한다.\n"
    second = "# 수행 방법\n\n시험 절차를 정립한다.\n"
    version = _version(tmp_path, [_section(first), _section(second, "1", "수행 방법")])

    def drop_figure(text: str, call_index: int) -> str:
        if call_index == 1:
            return text.replace("[[FIG:fig-s1-01]]", "", 1)
        return text

    transport = RecordingTransport(drop_figure)
    _configure(monkeypatch, tmp_path)

    result = refine_version("demo", transport=transport, host="codex-oauth")

    assert result.refined is False
    assert result.reason == "no-content-changed"
    assert not result.output_path.exists()
    chunk = _reported_chunk(version)
    assert "figure-tokens" in _object_list(chunk["failed_invariants"])


def _long_markdown(size: int = 25_000) -> str:
    headings = [f"## 세부 연구 {index}\n\n" for index in range(1, 5)]
    remaining = size - sum(len(heading) for heading in headings)
    targets = [remaining // 4] * 4
    targets[-1] += remaining - sum(targets)
    parts: list[str] = []
    for heading, target in zip(headings, targets, strict=True):
        body = "가" * (target - 5) + "한다.\n\n"
        parts.extend((heading, body))
    text = "".join(parts)
    assert len(text) == size
    return text


def test_twenty_five_thousand_chars_chunk_losslessly_on_headings() -> None:
    original = _long_markdown()

    chunks = chunk_markdown(original)
    result = refine_section(original, RecordingTransport(), host="codex-oauth")

    assert len(chunks) >= 4
    assert all(len(chunk.text) <= 9_000 for chunk in chunks)
    assert all(
        any(line.strip() and not line.lstrip().startswith("#") for line in chunk.text.splitlines())
        for chunk in chunks
    )
    assert all(chunk.text.startswith("## ") for chunk in chunks)
    assert "".join(chunk.text for chunk in chunks) == original
    assert result.text == original
    assert len(result.chunks) == len(chunks)


def test_ten_thousand_chars_stay_on_the_single_call_path() -> None:
    text = "가" * 9_997 + "한다."

    chunks = chunk_markdown(text)

    assert len(text) == 10_000
    assert len(chunks) == 1
    assert chunks[0].text == text


def test_decimal_pressure_draft_refines_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sections = [
        _section("# 연구 목표\n\n압력은 3.5 MPa로 관리한다.\n"),
        _section("# 수행 방법\n\n시험 절차를 정립한다.\n", "1", "수행 방법"),
        _section("# 기대 효과\n\n성과를 현장에 적용한다.\n", "2", "기대 효과"),
    ]
    _ = _version(tmp_path, sections)
    _configure(monkeypatch, tmp_path)

    result = refine_version(
        "demo", transport=RecordingTransport(), host="codex-oauth"
    )

    assert result.refined is False
    assert result.reason == "no-content-changed"
    assert result.failed_chunks == 0
    assert not result.output_path.exists()


def test_residual_sentinel_rejects_chunk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = "# 연구 목표\n\n시스템을 검증한다.\n"
    second = "# 수행 방법\n\n시험 절차를 정립한다.\n"
    version = _version(tmp_path, [_section(first), _section(second, "1", "수행 방법")])

    def append_sentinel(text: str, call_index: int) -> str:
        return text + ("@@IMMUTABLE_9999@@" if call_index == 1 else "")

    _configure(monkeypatch, tmp_path)
    result = refine_version(
        "demo", transport=RecordingTransport(append_sentinel), host="codex-oauth"
    )

    assert result.failed_chunks == 1
    chunk = _reported_chunk(version)
    assert "residual-sentinels" in _object_list(chunk["failed_invariants"])


def test_all_chunks_with_residual_sentinel_exit_nonzero_and_leave_input_untouched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    body = "# 연구 목표\n\n시스템을 검증한다.\n"
    version = _version(tmp_path, [_section(body)])
    drafts = version / "out" / "drafts.json"
    before = drafts.read_bytes()
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("PROPOSAL_REFINE_TRANSPORT", "fake")
    monkeypatch.setenv("PROPOSAL_REFINE_FAKE_MODE", "residual-sentinel")

    rc = proposal_refine.main(["--slug", "demo", "--json"])

    assert rc == REFINEMENT_INVARIANT_FAILED_EXIT
    assert "REFINEMENT_INVARIANT_FAILED" in capsys.readouterr().err
    assert drafts.read_bytes() == before
    assert not (version / "out" / "drafts.refined.json").exists()
    assert _report(version)["failed_chunks"] == 1


def test_preexisting_style_violation_passes_through_and_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sections = [
        _section("# 연구 목표\n\n내년에 적용할 예정이다.\n"),
        _section("# 수행 방법\n\n시험 절차를 정립한다.\n", "1", "수행 방법"),
        _section("# 기대 효과\n\n성과를 현장에 적용한다.\n", "2", "기대 효과"),
    ]
    version = _version(tmp_path, sections)
    _configure(monkeypatch, tmp_path)

    rc = proposal_refine.main(
        ["--slug", "demo", "--json"], transport=RecordingTransport()
    )

    assert rc == 0
    assert not (version / "out" / "drafts.refined.json").exists()
    assert _report(version)["source_equals_output"] is True
    assert "PROPOSAL-REFINED" not in capsys.readouterr().err
    violations = _object_list(_report(version)["preexisting_style_violations"])
    assert violations == [
        {
            "code": "forbidden-expression",
            "column": 7,
            "line": 1,
            "section_id": "0",
            "token": "할 예정",
        }
    ]


def test_new_style_violation_rejects_clean_chunk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = "# 연구 목표\n\n시스템을 검증한다.\n"
    second = "# 수행 방법\n\n시험 절차를 정립한다.\n"
    _ = _version(tmp_path, [_section(first), _section(second, "1", "수행 방법")])

    def add_forbidden_expression(text: str, call_index: int) -> str:
        if call_index == 1:
            return text.replace("검증한다", "아마도 검증한다")
        return text

    _configure(monkeypatch, tmp_path)
    result = refine_version(
        "demo",
        transport=RecordingTransport(add_forbidden_expression),
        host="codex-oauth",
    )

    assert result.failed_chunks == 1
    assert result.refined is False
    assert result.reason == "no-content-changed"
    assert not result.output_path.exists()
    chunk = _reported_chunk(result.report_path.parents[1])
    assert "kimm-style" in _object_list(chunk["failed_invariants"])


def test_proposal_cli_identity_transport_reports_explicit_no_op(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    body = "# 연구 목표\n\n시스템을 검증한다.\n"
    version = _version(tmp_path, [_section(body)])
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("PROPOSAL_REFINE_TRANSPORT", "fake")

    assert proposal_cli.main(["refine", "--slug", "demo", "--json"]) == 0

    payload = cast(object, json.loads(capsys.readouterr().out))
    assert isinstance(payload, dict)
    result = cast(dict[object, object], payload)
    assert result["chunk_count"] == 1
    assert result["invariants"] == "NO_CHANGE"
    assert result["refined"] is False
    assert result["reason"] == "no-content-changed"
    assert not (version / "out" / "drafts.refined.json").exists()
    report = _report(version)
    assert report["no_op_detected"] is True
    assert report["changed_sentence_count"] == 0
    assert report["source_equals_output"] is True
    assert report["rules_applied"] == []


def test_patent_section_refuses_glm_before_transport() -> None:
    transport = RecordingTransport()
    body = "# 발명 개요\n\n특허 출원 전략을 수립한다.\n"

    with pytest.raises(RouteRefused, match="owner-controlled host"):
        _ = refine_section(body, transport, host="litellm-glm")

    assert transport.calls == []


def test_no_non_glm_host_skips_without_failing_and_records_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = "# 발명 개요\n\n특허 출원 전략을 수립한다.\n"
    version = _version(tmp_path, [_section(body)])
    drafts = version / "out" / "drafts.json"
    original = drafts.read_bytes()
    transport = RecordingTransport()
    _configure(monkeypatch, tmp_path)

    result = refine_version("demo", transport=transport, host="litellm-glm")

    assert result.refined is False
    assert result.reason == "route-refused"
    assert transport.calls == []
    assert drafts.read_bytes() == original
    assert not result.output_path.exists()
    report = _report(version)
    assert report["no_op_detected"] is True
    assert report["failure_reason"] == "route-refused"
    assert report["source_equals_output"] is True
    manifest = _json_object(version / "manifest.json")
    assert manifest["refined"] is False
    assert manifest["reason"] == "route-refused"


def test_heading_invariant_rejects_tampered_host_output() -> None:
    body = "# 원래 제목\n\n본문을 검증한다.\n"
    transport = RecordingTransport(
        lambda text, _index: _replace_token(text, 0, "# 바뀐 제목\n")
    )

    with pytest.raises(RefinementInvariantFailed) as raised:
        _ = refine_section(body, transport, host="codex-oauth")

    result = raised.value.result
    assert result is not None
    assert result.text == body
    assert "headings" in result.chunks[0].failed_invariants


def test_quotation_invariant_rejects_tampered_host_output() -> None:
    body = '# 연구 목표\n\n연구자는 "원문 인용"이라고 설명했다.\n'
    transport = RecordingTransport(
        lambda text, _index: _replace_token(text, 1, '"변경 인용"')
    )

    with pytest.raises(RefinementInvariantFailed) as raised:
        _ = refine_section(body, transport, host="codex-oauth")

    result = raised.value.result
    assert result is not None
    assert result.text == body
    assert "quotations" in result.chunks[0].failed_invariants


def test_table_structure_invariant_rejects_tampered_host_output() -> None:
    body = (
        "# 연구 목표\n\n"
        "| 항목 | 값 |\n"
        "| --- | --- |\n"
        "| 오차 | 10 mm |\n\n"
        "본문을 검증한다.\n"
    )
    transport = RecordingTransport(
        lambda text, _index: _replace_token(text, 1, "| 변경 항목 | 변경 값 |\n")
    )

    with pytest.raises(RefinementInvariantFailed) as raised:
        _ = refine_section(body, transport, host="codex-oauth")

    result = raised.value.result
    assert result is not None
    assert result.text == body
    assert "table-caption-structure" in result.chunks[0].failed_invariants


def test_masking_is_deterministic_and_protected_spans_never_reach_host() -> None:
    body = (
        "# 연구 목표\n\n"
        "KIMM은 10 mm 오차를 관리하며 \"직접 인용\"을 보존한다. "
        "[[FIG:fig-s1-01]]을 참조한다. [C01]\n\n"
        "TRL 4: 실증 단계를 유지한다.\n"
        "| KPI | 목표 |\n| --- | --- |\n| 오차 | 10 mm |\n"
    )

    first = mask_immutables(body)
    second = mask_immutables(body)
    transport = RecordingTransport()
    _ = refine_section(body, transport, host="codex-oauth")

    assert first == second
    assert first.text.encode("utf-8") == second.text.encode("utf-8")
    assert [entry.token for entry in first.registry] == [
        f"@@IMMUTABLE_{index:04d}@@" for index in range(1, len(first.registry) + 1)
    ]
    sent = transport.calls[0][0]
    protected_values = (
        "# 연구 목표",
        "KIMM",
        "10 mm",
        '"직접 인용"',
        "TRL 4: 실증 단계를 유지한다.",
        "| KPI | 목표 |",
        "[C01]",
    )
    assert all(value not in sent for value in protected_values)
    assert "[[FIG:fig-s1-01]]" in sent
    assert transport.calls[0][1:] == ("codex-oauth", proposal_refine.DEFAULT_TIMEOUT_SECONDS)


def test_invalid_draft_bundle_still_leaves_a_parseable_failure_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    version = _version(tmp_path, [_section("본문을 검증한다.")])
    _ = (version / "out" / "drafts.json").write_text("not-json", encoding="utf-8")
    _configure(monkeypatch, tmp_path)

    with pytest.raises(RefinementInputError):
        _ = refine_version("demo", transport=RecordingTransport(), host="codex-oauth")

    report = _report(version)
    assert report["refined"] is False
    assert report["no_op_detected"] is True
    assert report["failure_reason"] == "input-error"
    assert report["schema_version"] == 2


def test_live_host_unavailable_is_explicit_and_has_no_refined_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    version = _version(tmp_path, [_section("본문을 검증한다.")])
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("PROPOSAL_REFINE_TRANSPORT", "live")
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    result = refine_version("demo")

    assert result.refined is False
    assert result.reason == "host-unavailable"
    assert not result.output_path.exists()
    report = _report(version)
    assert report["failure_reason"] == "host-unavailable"
    assert report["source_equals_output"] is True


def test_hwpx_xml_is_refused_before_transport() -> None:
    transport = RecordingTransport()

    with pytest.raises(RefinementInputError, match="HWPX XML"):
        _ = refine_section("<hp:p>본문이다.</hp:p>", transport, host="codex-oauth")

    assert transport.calls == []


def test_verifier_runs_all_eleven_named_invariants() -> None:
    original = '# 제목\n\nKIMM은 2026-08-22에 10 mm 오차를 검증한다. "인용" [C01]\n'

    checks = verify_invariants(original, original, char_budget=1_000)

    assert [check.name for check in checks] == list(proposal_refine.INVARIANT_NAMES)
    assert len(checks) == 11
    assert all(check.passed for check in checks)


def test_proper_noun_citation_and_style_invariants_reject_drift() -> None:
    original = "KIMM 연구진은 충분한 근거로 시스템을 검증한다. [C01]"
    candidate = "KIM 연구진은 충분한 근거로 시스템을 검증한 것 같다."

    failures = {
        check.name for check in verify_invariants(original, candidate) if not check.passed
    }

    assert {"proper-nouns", "citations", "kimm-style"}.issubset(failures)


def test_structured_prefix_does_not_mask_the_rest_of_a_prose_line() -> None:
    body = "KPI-1: 오차 기준을 지킨다. 검증 절차는 현장에서 반복 수행한다."
    transport = RecordingTransport(
        lambda text, _index: text.replace("검증 절차는", "현장에서는 검증 절차를")
    )

    result = refine_section(body, transport, host="codex-oauth")

    assert len(transport.calls) == 1
    assert result.text != body
    assert result.chunks[0].sent is True


def test_changed_refinement_report_counts_sentences_and_rules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = "# 연구 목표\n\n시스템을 면밀하게 검증한다.\n"
    version = _version(tmp_path, [_section(body)])
    _configure(monkeypatch, tmp_path)
    transport = RecordingTransport(
        lambda text, _index: text.replace("면밀하게", "꼼꼼히")
    )

    result = refine_version("demo", transport=transport, host="codex-oauth")

    assert result.refined is True
    report = _report(version)
    assert report["no_op_detected"] is False
    assert report["source_equals_output"] is False
    assert report["changed_sentence_count"] == 1
    rules = _object_list(report["rules_applied"])
    assert "korean-technical-prose" in rules


def test_empty_and_table_only_sections_are_deterministic_passthrough() -> None:
    transport = RecordingTransport()
    table = "항목 | 값\n--- | ---\n오차 | 10 mm\n"

    empty_result = refine_section("", transport, host="codex-oauth")
    table_result = refine_section(table, transport, host="codex-oauth")

    assert empty_result.text == ""
    assert table_result.text == table
    assert transport.calls == []
    assert all(chunk.sent is False for chunk in (*empty_result.chunks, *table_result.chunks))


# ---------------------------------------------------------------------------
# 그림 인용 문체 (소유자 지시 2026-08-28): "그림 N은 ...를 나타낸다"처럼 그림을
# 주어로 세우지 않는다. 단락의 목적을 뒷받침하는 주장 문장을 쓰고 그림은 그 근거로
# 괄호에 표기한다 — "...를 개발한다 (그림 N)."


def test_recast_turns_a_figure_subject_sentence_into_a_claim_with_citation() -> None:
    from skills.proposal.scripts.proposal_refine import recast_figure_citations

    text = (
        "- [[FIG:fig-s0-01]]은 현장 관측값과 목표 지형을 안전 정지 조건이 "
        "포함된 작업 계약으로 연결하는 구조를 나타낸다."
    )

    recast, count = recast_figure_citations(text)

    assert count == 1
    assert recast == (
        "- 현장 관측값과 목표 지형을 안전 정지 조건이 포함된 작업 계약으로 "
        "연결하는 구조를 개발한다 ([[FIG:fig-s0-01]])."
    )


def test_recast_handles_a_copula_figure_sentence() -> None:
    from skills.proposal.scripts.proposal_refine import recast_figure_citations

    text = "- [[FIG:fig-s1-01]]은 측량 갱신 결과가 토공 계획의 지형 입력으로 되돌아가는 폐루프 구조다."

    recast, count = recast_figure_citations(text)

    assert count == 1
    assert recast == (
        "- 측량 갱신 결과가 토공 계획의 지형 입력으로 되돌아가는 폐루프 구조를 "
        "개발한다 ([[FIG:fig-s1-01]])."
    )


def test_recast_picks_a_verb_by_the_claims_head_noun() -> None:
    from skills.proposal.scripts.proposal_refine import recast_figure_citations

    shown, shown_count = recast_figure_citations(
        "- [[FIG:fig-s2-01]]은 세 지표의 측정 지점과 반복 시험 절차를 함께 보여준다."
    )
    path, path_count = recast_figure_citations(
        "- [[FIG:fig-s4-01]]은 검증된 자율 토공 기술이 현장 생산성과 작업자 "
        "안전으로 확산되는 경로를 나타낸다."
    )

    assert shown_count == path_count == 1
    assert shown == "- 세 지표의 측정 지점과 반복 시험 절차를 함께 적용한다 ([[FIG:fig-s2-01]])."
    assert path == (
        "- 검증된 자율 토공 기술이 현장 생산성과 작업자 안전으로 확산되는 경로를 "
        "제시한다 ([[FIG:fig-s4-01]])."
    )


def test_recast_leaves_non_matching_and_already_recast_lines_alone() -> None:
    from skills.proposal.scripts.proposal_refine import recast_figure_citations

    text = "\n".join(
        (
            "- [[FIG:fig-s3-02]] 구성 개요",
            "- 목표 지형과 장비 제약을 만족하는 궤적을 생성한다 ([[FIG:fig-s3-04]]).",
            "- 일반 문장은 그대로 남는다.",
        )
    )

    recast, count = recast_figure_citations(text)

    assert count == 0
    assert recast == text


def test_refine_recasts_figure_citations_before_the_host_sees_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    figure_line = "- [[FIG:fig-s0-01]]은 관측값을 작업 계약으로 연결하는 구조를 나타낸다."
    version = _version(tmp_path, [_section(f"## 개요\n\n{figure_line}\n")])
    _configure(monkeypatch, tmp_path)
    transport = RecordingTransport()

    result = refine_version("demo", transport=transport, host="codex-oauth")

    assert result.refined
    refined = _json_object(version / "out" / "drafts.refined.json")
    body = cast(str, _mapping(_object_list(refined["sections"])[0])["body"])
    assert "관측값을 작업 계약으로 연결하는 구조를 개발한다 ([[FIG:fig-s0-01]])." in body
    assert "나타낸다" not in body
    assert all("나타낸다" not in sent_text for sent_text, _host, _timeout in transport.calls)
    report = _report(version)
    assert report["figure_citation_recasts"] == 1
