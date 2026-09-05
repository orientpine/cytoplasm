from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path

import pytest

from automation import codex_llm
from skills.doctype.scripts import (
    doctype_cli,
    doctype_extract,
    doctype_generate,
    doctype_llm,
    doctype_routing,
    doctype_save,
    doctype_store,
    make_fixtures,
)


REPO = Path(__file__).resolve().parents[2]


def _log_records(path: Path) -> list[dict[str, str | bool]]:
    records: list[dict[str, str | bool]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise AssertionError("LLM audit record must be an object")
        record = {key: value for key, value in raw.items() if isinstance(key, str) and isinstance(value, (str, bool))}
        records.append(record)
    return records


def _codex_stub(path: Path) -> Path:
    stub = path / "codex-stub"
    source = '''#!/usr/bin/env python3
import sys
argv = sys.argv[1:]
prompt = argv[argv.index("-z") + 1] if "-z" in argv else ""
if "DOCTYPE_STAGE=EXTRACT" in prompt:
    print('{"gist":"구조화된 추천 근거","tone":"공식적","mode":"narrative","sections":[{"title":"추천 대상","guidance":"사실 식별","kind":"slot-fill"},{"title":"추천 사유","guidance":"논증 작성","kind":"narrative"},{"title":"선정 근거","guidance":"근거 종합","kind":"narrative"}]}')
else:
    print("입력 사실을 연결하여 수행 역량과 일정 대응 근거를 갖춘 업체로 추천합니다.")
'''
    _ = stub.write_text(source, encoding="utf-8")
    _ = stub.chmod(0o755)
    return stub


def _store(tmp_path: Path) -> doctype_store.DocTypeStore:
    return doctype_store.DocTypeStore(
        doctype_store.StorePaths(
            canonical_root=tmp_path / "repo" / "doctype" / "library",
            overlay_root=tmp_path / "overlay",
            private_root=tmp_path / "private",
            rules_file=REPO / "skills" / "doctype" / "configs" / "sensitivity-rules.yaml",
        ),
        clock=lambda: "2026-07-17T00:00:00Z",
    )


def _prepared_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    sensitive: bool = False,
) -> tuple[doctype_store.DocTypeStore, Path, Path]:
    monkeypatch.setenv("DOCTYPE_HERMES_BIN", str(_codex_stub(tmp_path)))
    monkeypatch.setenv("DOCTYPE_LLM_LOG", str(tmp_path / "logs" / "calls.jsonl"))
    _, example = make_fixtures.make(tmp_path / "examples")
    if sensitive:
        _ = example.write_text(
            example.read_text(encoding="utf-8") + "\n특허 출원 검토\n",
            encoding="utf-8",
        )
    store = _store(tmp_path)
    extracted = doctype_extract.extract(example, store.paths.rules_file, mode_override="narrative")
    _ = store.add(extracted.draft("vendor-reason", "업체추천사유서"))
    monkeypatch.setattr(doctype_cli, "_store", lambda: store)
    inputs = tmp_path / "inputs.json"
    payload = json.dumps({"업체명": "합성 수행사", "사업명": "합성 과업"})
    _ = inputs.write_text(payload, encoding="utf-8")
    return store, example, inputs


def _draft_args(tmp_path: Path, inputs: Path, save_request: str) -> argparse.Namespace:
    return argparse.Namespace(
        name="업체추천사유서",
        inputs_json=str(inputs),
        out=str(tmp_path / "drafts" / "vendor.md"),
        review=False,
        save_request=save_request,
    )


def test_register_generate_and_refine_when_narrative_example_is_approved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCTYPE_HERMES_BIN", str(_codex_stub(tmp_path)))
    monkeypatch.setenv("DOCTYPE_LLM_LOG", str(tmp_path / "logs" / "calls.jsonl"))
    _, example = make_fixtures.make(tmp_path / "examples")
    store = _store(tmp_path)

    extracted = doctype_extract.extract(example, store.paths.rules_file, mode_override="narrative")
    first = store.add(extracted.draft("vendor-reason", "업체추천사유서"))
    inputs = {"업체명": "합성 수행사", "사업명": "합성 과업"}
    draft = doctype_generate.generate(store, first.entry, inputs, tmp_path / "drafts" / "vendor.md")
    refined = doctype_extract.extract(
        draft.path, store.paths.rules_file, mode_override="narrative", prior=first.entry.metadata
    )
    second = store.add_version(refined.draft("vendor-reason", "업체추천사유서"))

    assert first.entry.metadata.version == 1
    assert first.private_path.parent.stat().st_mode & 0o777 == 0o700
    assert "수행 역량과 일정 대응 근거" in draft.path.read_text(encoding="utf-8")
    assert draft.narrative_sections == ("section-02", "section-03")
    assert second.entry.metadata.version == 2
    assert len(second.entry.metadata.examples) == 2
    assert "합성 수행사" not in second.path.read_text(encoding="utf-8")


def test_sensitive_example_when_registered_never_leaves_the_codex_tier_or_logs_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = "PRIVATE-CANARY-" + secrets.token_hex(4)
    source = tmp_path / "sensitive.md"
    _ = source.write_text(f"## 검토\n특허 출원 검토 {canary}\n", encoding="utf-8")
    monkeypatch.setenv("DOCTYPE_HERMES_BIN", str(_codex_stub(tmp_path)))
    log = tmp_path / "logs" / "calls.jsonl"
    monkeypatch.setenv("DOCTYPE_LLM_LOG", str(log))
    store = _store(tmp_path)

    extracted = doctype_extract.extract(source, store.paths.rules_file)
    result = store.add(extracted.draft("sensitive-reason", "민감서류"))

    # A route that is not the pinned Codex OAuth tier (here: argv without the load-bearing
    # --ignore-user-config, which is what keeps a configured fallback from firing) must be
    # refused before a byte of the document leaves the node.
    monkeypatch.setattr(
        codex_llm.CodexClient,
        "argv",
        lambda self, prompt: [self.binary, "-z", prompt, "--provider", "custom:other"],
    )
    with pytest.raises(doctype_llm.PatentRoutingError):
        doctype_llm.call_codex("must fail before transport", sensitive=True)
    records = _log_records(log)
    assert result.entry.metadata.sensitivity == "patent-sensitive"
    assert result.private_path.read_text(encoding="utf-8").endswith(canary + "\n")
    assert all(record.get("provider") == "openai-codex" for record in records)
    assert all(canary not in line for line in log.read_text(encoding="utf-8").splitlines())


def test_slot_fill_when_service_order_has_only_fields_avoids_narrative_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCTYPE_HERMES_BIN", str(_codex_stub(tmp_path)))
    log = tmp_path / "logs" / "calls.jsonl"
    monkeypatch.setenv("DOCTYPE_LLM_LOG", str(log))
    service, _ = make_fixtures.make(tmp_path / "examples")
    store = _store(tmp_path)

    extracted = doctype_extract.extract(service, store.paths.rules_file, mode_override="slot-fill")
    registered = store.add(extracted.draft("service-order", "용역지시서"))
    result = doctype_generate.generate(
        store,
        registered.entry,
        {"과업명": "합성 용역", "수행업체": "합성 수행사", "수행기간": "3일", "과업범위": "검증", "산출물": "결과서"},
        tmp_path / "drafts" / "service.md",
    )

    assert result.narrative_sections == ()
    assert "- 과업명: 합성 용역" in result.path.read_text(encoding="utf-8")
    assert [record.get("purpose") for record in _log_records(log)] == ["gist-extract"]


def test_generate_when_output_is_under_metadata_repo_refuses(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(doctype_generate.GenerationError):
        doctype_generate.ensure_private_output(store, store.repo_root() / "draft.md")


def test_codex_call_fails_closed_when_oauth_credentials_are_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing credentials is a refusal, never a downgrade: one attempt, no audit record."""
    # Given
    calls = tmp_path / "calls.log"
    stub = tmp_path / "hermes-no-credentials"
    _ = stub.write_text(
        "#!/bin/sh\n"
        f"printf 'call\\n' >> '{calls}'\n"
        "printf '%s' 'hermes -z: agent failed: No Codex credentials stored.' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    _ = stub.chmod(0o755)
    monkeypatch.setenv("DOCTYPE_HERMES_BIN", str(stub))
    log = tmp_path / "logs" / "calls.jsonl"
    monkeypatch.setenv("DOCTYPE_LLM_LOG", str(log))

    # When
    with pytest.raises(doctype_llm.LlmCallError):
        _ = doctype_llm.call_codex("문서 본문", purpose="gist-extract")

    # Then
    assert calls.read_text(encoding="utf-8") == "call\n"
    assert not log.exists()


def test_register_from_example_when_save_route_is_ambiguous_has_zero_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    monkeypatch.setenv("DOCTYPE_HERMES_BIN", str(_codex_stub(tmp_path)))
    store = _store(tmp_path)
    monkeypatch.setattr(doctype_cli, "_store", lambda: store)
    _, example = make_fixtures.make(tmp_path / "examples")
    args = argparse.Namespace(name="업체추천사유서", example=str(example), mode="narrative")
    args.save_request = "옵시디언이나 드라이브 중 하나에 저장해줘"

    # When
    exit_code = doctype_cli.cmd_register(args)

    # Then
    assert exit_code == 5
    assert store.list() == ()
    assert "SAVE-CLARIFY" in capsys.readouterr().out


def test_draft_when_save_route_is_ambiguous_has_zero_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    _, _, inputs = _prepared_cli(tmp_path, monkeypatch)
    output = tmp_path / "drafts" / "vendor.md"
    args = _draft_args(tmp_path, inputs, "옵시디언이나 드라이브 중 하나에 저장해줘")

    # When
    exit_code = doctype_cli.cmd_draft(args)

    # Then
    assert exit_code == 5
    assert not output.exists()
    assert "SAVE-CLARIFY" in capsys.readouterr().out


def test_refine_when_save_route_is_ambiguous_has_zero_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    store, example, _ = _prepared_cli(tmp_path, monkeypatch)
    args = argparse.Namespace(
        name="업체추천사유서",
        approved=str(example),
        note=None,
        save_request="옵시디언이나 드라이브 중 하나에 저장해줘",
    )

    # When
    exit_code = doctype_cli.cmd_refine(args)

    # Then
    assert exit_code == 5
    assert tuple(entry.metadata.version for entry in store.list()) == (1,)
    assert "SAVE-CLARIFY" in capsys.readouterr().out


def test_draft_when_route_is_obsidian_never_calls_drive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    _, _, inputs = _prepared_cli(tmp_path, monkeypatch)
    save_calls: list[tuple[Path, doctype_routing.SaveRoute]] = []

    def save(file: Path, route: doctype_routing.SaveRoute) -> None:
        save_calls.append((file, route))

    monkeypatch.setattr(doctype_save, "save_from_environment", save)

    # When
    exit_code = doctype_cli.cmd_draft(_draft_args(tmp_path, inputs, "이 내용을 개인노트 저장해줘"))

    # Then
    assert exit_code == 0
    assert save_calls == [
        (tmp_path / "drafts" / "vendor.md", doctype_routing.SaveRoute(("obsidian",), "personal-note", False))
    ]
    assert "drive=" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("save_request", "sensitive"),
    [
        pytest.param("보고서는 만들어줘. 저장하지 마", False, id="none"),
        pytest.param("Drive에 보고서를 저장해줘", True, id="gated"),
    ],
)
def test_draft_when_route_forbids_drive_never_calls_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    save_request: str,
    sensitive: bool,
) -> None:
    # Given
    _, _, inputs = _prepared_cli(tmp_path, monkeypatch, sensitive=sensitive)
    save_calls: list[tuple[Path, doctype_routing.SaveRoute]] = []

    def save(file: Path, route: doctype_routing.SaveRoute) -> None:
        save_calls.append((file, route))

    monkeypatch.setattr(doctype_save, "save_from_environment", save)

    # When
    exit_code = doctype_cli.cmd_draft(_draft_args(tmp_path, inputs, save_request))

    # Then
    assert exit_code == 0
    assert save_calls == []


def test_draft_when_route_contains_drive_keeps_current_publish_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    _, _, inputs = _prepared_cli(tmp_path, monkeypatch)
    save_calls: list[tuple[Path, doctype_routing.SaveRoute]] = []

    def save(file: Path, route: doctype_routing.SaveRoute) -> None:
        save_calls.append((file, route))

    monkeypatch.setattr(doctype_save, "save_from_environment", save)

    # When
    exit_code = doctype_cli.cmd_draft(_draft_args(tmp_path, inputs, "주간 보고서를 파일로 만들어줘"))

    # Then
    assert exit_code == 0
    assert save_calls == [
        (tmp_path / "drafts" / "vendor.md", doctype_routing.SaveRoute(("drive",), "default-drive", False))
    ]
    assert "drive=verified" in capsys.readouterr().out
