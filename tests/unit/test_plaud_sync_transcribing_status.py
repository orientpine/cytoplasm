"""``transcribing`` — the plaud record status between discovery and the approval card.

Local transcription (2026-09-04, owner request) needs a slot the FSM never had: the
recording is known, its cloud draft is frozen, but the node has not yet produced its
own diarized transcript. New file on purpose: the existing plaud test files are the
per-module suites and this status cuts across model, sync, watch_step and the status
skill (tests/AGENTS.md — new checks go in new files).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Final, NoReturn

import pytest

from automation.plaud_sync.lifelog_model import ExtractionSkipped
from automation.plaud_sync.model import (
    PlaudSyncRecord,
    PlaudSyncState,
    parse_record,
    serialize_record,
)
from automation.plaud_sync.note import LifelogRecording
from automation.plaud_sync.sync import plan_new_records
from automation.plaud_sync.watch_step import ResolveEffects, resolve_tick

_REPO: Final = Path(__file__).resolve().parents[2]
_CLI: Final = _REPO / "skills" / "plaud" / "scripts" / "plaud_cli.py"
_NOW: Final = datetime(2026, 9, 4, 4, 0, 0, tzinfo=UTC)

_BASE: Final = PlaudSyncRecord(
    version=1,
    recording_id="rec-001",
    recorded_at="2026-09-01T08:00:00Z",
    note_relpath="000_PARA/Area/Lifelog/2026/2026-09-01-standup--abcdef123456.md",
    note_title="standup (2026-09-01)",
    body_sha256="a" * 64,
    action_hash=f"sha256:{'b' * 64}",
    status="transcribing",
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

_RECORDING: Final = LifelogRecording(
    id="rec-001",
    name="standup",
    created_at="2026-09-01T08:05:00Z",
    start_at="2026-09-01T08:00:00Z",
    duration_ms=60000,
    summary_markdown="- 결정",
    transcript_text="말씀",
)


def _boom(*_args: object) -> NoReturn:
    raise AssertionError("resolve_tick must not touch a transcribing record")




def test_record_when_status_transcribing_then_parses_and_defaults_attempts_to_zero() -> None:
    row = serialize_record(_BASE)
    assert row["status"] == "transcribing"
    assert "transcribe_attempts" not in row, "zero attempts must not change old state bytes"
    parsed = parse_record(row)
    assert parsed == _BASE
    assert parsed.transcribe_attempts == 0


def test_record_when_attempts_nonzero_then_roundtrips_through_serialize_and_parse() -> None:
    record = replace(_BASE, transcribe_attempts=2)
    row = serialize_record(record)
    assert row["transcribe_attempts"] == 2
    assert parse_record(json.loads(json.dumps(row))) == record


def test_record_when_attempts_is_not_an_integer_then_parse_refuses() -> None:
    row = serialize_record(_BASE)
    row["transcribe_attempts"] = "2"
    with pytest.raises(ValueError, match="transcribe_attempts"):
        parse_record(row)




def _skip(recording: LifelogRecording) -> ExtractionSkipped:
    return ExtractionSkipped("테스트")


def test_plan_new_records_when_initial_status_transcribing_then_records_start_there() -> None:
    result = plan_new_records(
        PlaudSyncState(1, None, {}),
        [_RECORDING],
        now=_NOW,
        policy_version=8,
        initial_status="transcribing",
        extractor=_skip,
    )
    record = result.state.records["rec-001"]
    assert record.status == "transcribing"
    assert record.transcribe_attempts == 0
    assert result.planned == ("rec-001",)
    assert "\n## 요약\n" in result.bodies["rec-001"]


def test_plan_new_records_when_initial_status_omitted_then_still_planned() -> None:
    result = plan_new_records(
        PlaudSyncState(1, None, {}),
        [_RECORDING],
        now=_NOW,
        policy_version=8,
        extractor=_skip,
    )
    assert result.state.records["rec-001"].status == "planned"




def test_resolve_tick_when_record_is_transcribing_then_no_effect_runs_and_state_is_kept() -> None:
    state = PlaudSyncState(1, None, {"rec-001": _BASE})
    effects = ResolveEffects(
        post_approval=_boom, probe_reaction=_boom, write_obsidian=_boom, notify_result=_boom, now=_NOW
    )
    result = resolve_tick(state, effects=effects)
    assert result.state.records["rec-001"] == _BASE
    assert (result.posted, result.written, result.abandoned) == ((), (), ())




def _cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("plaud_cli_transcribing", _CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _state_payload(tmp_path: Path, **records: dict[str, object]) -> Path:
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps({"version": 1, "last_poll_at": "2026-09-04T03:00:00Z", "records": records}),
        encoding="utf-8",
    )
    return state


def test_status_when_records_are_transcribing_then_counts_attempts_reason_and_transcripts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _cli()
    waiting = dict(serialize_record(replace(_BASE, transcribe_attempts=1, last_block_reason="rc=4 로컬 도구 없음")))
    done = dict(
        serialize_record(
            replace(
                _BASE,
                recording_id="rec-002",
                status="planned",
                note_relpath="000_PARA/Area/Lifelog/2026/2026-09-02-b--abcdef654321.md",
            )
        )
    )
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    (transcripts / "2026-09-02-b--abcdef654321.md").write_text("# b 전사본\n", encoding="utf-8")
    state = _state_payload(tmp_path, **{"rec-001": waiting, "rec-002": done})

    assert cli.main(["status", "--state", str(state)]) == 0
    human = capsys.readouterr().out
    assert "transcribing 1" in human
    assert "전사 대기(transcribing) 1건" in human
    assert "rec-001 · 시도 1 · 사유 rc=4 로컬 도구 없음" in human
    assert "로컬 전사본 1건" in human
    assert "rec-002 · 2026-09-02-b--abcdef654321.md" in human

    assert cli.main(["status", "--state", str(state), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["transcribing"] == 1
    assert payload["transcribing"] == [
        {"recording_id": "rec-001", "attempts": 1, "reason": "rc=4 로컬 도구 없음"}
    ]
    assert payload["transcripts_dir"] == str(transcripts)
    assert payload["transcripts"] == [
        {
            "recording_id": "rec-002",
            "status": "planned",
            "path": str(transcripts / "2026-09-02-b--abcdef654321.md"),
        }
    ]
