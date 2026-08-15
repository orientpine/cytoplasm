from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from automation.twin_observe.aggregate import ActionTally, aggregate_events
from automation.twin_observe.ledgers import GateEvent, LedgerSource, Verdict, read_ledgers
from automation.twin_observe.propose import (
    AdvisoryAuthorityError,
    ObservedCandidate,
    WikiDraftRequest,
    build_candidates,
    render_draft,
    submit_candidates,
)


_TS = "2026-07-21T10:00:00Z"
_SYNTHETIC_BODY = "SYNTHETIC-LEDGER-BODY-MUST-NOT-LEAK"


def _record(action: str, status: str, timestamp: str = _TS) -> str:
    return json.dumps(
        {
            "action": action,
            "approval": {"channel": "dm", "method": "synthetic", "ref": "masked-ref"},
            "hash": "sha256:masked",
            "payload": {"message": _SYNTHETIC_BODY},
            "result": {"status": status},
            "target_id": "masked-target",
            "timestamp": timestamp,
        }
    )


def _source(tmp_path: Path, *lines: str) -> LedgerSource:
    path = tmp_path / "ledger.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return LedgerSource(name="wiki-audit", path=path)


def _event(verdict: Verdict, ts: str = _TS) -> GateEvent:
    return GateEvent(skill="calendar", action="create", verdict=verdict, ts=ts, ledger="approvals")


def _tally(rejects: int, approves: int) -> ActionTally:
    events = tuple([_event(Verdict.REJECT)] * rejects + [_event(Verdict.APPROVE)] * approves)
    return ActionTally(skill="calendar", action="create", approves=approves, rejects=rejects, events=events)


def test_reader_parses_valid_wiki_audit_record_into_gate_event(tmp_path: Path) -> None:
    # Given
    source = _source(tmp_path, _record("wiki.create", "saved"))

    # When
    result = read_ledgers((source,))

    # Then
    assert result.events == (
        GateEvent("wiki", "create", Verdict.APPROVE, _TS, "wiki-audit"),
    )
    assert result.skipped_lines == 0


def test_reader_parses_approval_record_with_message_id_schema(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "approvals.jsonl"
    record = json.loads(_record("calendar.create", "approved"))
    record["approval"] = {
        "channel": "approvals",
        "message_id": "masked-message-id",
        "method": "synthetic",
        "owner_id": "masked-owner-id",
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    # When
    result = read_ledgers((LedgerSource("approvals", path),))

    # Then
    assert result.events[0].skill == "calendar"
    assert result.events[0].action == "create"
    assert result.events[0].verdict is Verdict.APPROVE


def test_reader_skips_malformed_jsonl_line_and_counts_it(tmp_path: Path) -> None:
    # Given
    source = _source(tmp_path, _record("wiki.create", "saved"), "not-json")

    # When
    result = read_ledgers((source,))

    # Then
    assert len(result.events) == 1
    assert result.skipped_lines == 1


def test_reader_skips_schema_drift_without_crashing(tmp_path: Path) -> None:
    # Given
    source = _source(tmp_path, json.dumps({"action": "wiki.create"}))

    # When
    result = read_ledgers((source,))

    # Then
    assert result.events == ()
    assert result.skipped_lines == 1


def test_aggregate_counts_approve_and_reject_per_skill_and_action() -> None:
    # Given
    events = (_event(Verdict.REJECT), _event(Verdict.REJECT), _event(Verdict.APPROVE))

    # When
    tallies = aggregate_events(events)

    # Then
    assert tallies[0].skill == "calendar"
    assert tallies[0].action == "create"
    assert tallies[0].approves == 1
    assert tallies[0].rejects == 2


def test_aggregate_orders_skill_action_keys_deterministically() -> None:
    # Given
    events = (
        GateEvent("wiki", "edit", Verdict.APPROVE, _TS, "wiki-audit"),
        _event(Verdict.REJECT),
    )

    # When
    tallies = aggregate_events(events)

    # Then
    assert [(tally.skill, tally.action) for tally in tallies] == [
        ("calendar", "create"),
        ("wiki", "edit"),
    ]


def test_build_candidates_returns_candidate_at_three_rejects_and_zero_approves() -> None:
    # Given
    tally = _tally(rejects=3, approves=0)

    # When
    candidates = build_candidates((tally,))

    # Then
    assert candidates == (ObservedCandidate(tally),)


def test_build_candidates_returns_none_below_reject_threshold() -> None:
    # Given / When
    candidates = build_candidates((_tally(rejects=2, approves=0),))

    # Then
    assert candidates == ()


def test_build_candidates_returns_none_when_any_approval_exists() -> None:
    # Given / When
    candidates = build_candidates((_tally(rejects=3, approves=1),))

    # Then
    assert candidates == ()


def test_observed_candidate_rejects_non_advisory_authority() -> None:
    # Given
    tally = _tally(rejects=3, approves=0)

    # When / Then
    with pytest.raises(AdvisoryAuthorityError):
        ObservedCandidate(tally, authority="elevated")


def test_rendered_draft_declares_observed_advisory_principle() -> None:
    # Given
    candidate = ObservedCandidate(_tally(rejects=3, approves=0))

    # When
    payload = render_draft(candidate)

    # Then
    assert payload.kind == "principle"
    assert payload.provenance == "observed"
    assert payload.authority == "advisory"


def test_rendered_draft_is_advisory_and_masks_ledger_payloads() -> None:
    # Given
    candidate = ObservedCandidate(_tally(rejects=3, approves=0))

    # When
    payload = render_draft(candidate)

    # Then
    assert "경향일까요?" in payload.body
    assert "ledger=approvals timestamp=2026-07-21T10:00:00Z" in payload.body
    assert _SYNTHETIC_BODY not in payload.body
    assert "masked-target" not in payload.body
    assert "masked-ref" not in payload.body


def test_submit_candidates_calls_only_wiki_cli_draft_with_explicit_environment(tmp_path: Path) -> None:
    # Given
    candidate = ObservedCandidate(_tally(rejects=3, approves=0))
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def runner(argv: tuple[str, ...], environment: dict[str, str]) -> CompletedProcess[str]:
        calls.append((argv, environment))
        return CompletedProcess(argv, 0)

    request = WikiDraftRequest(
        wiki_cli=tmp_path / "wiki_cli.py",
        channel_id="dm",
        environment={"SAFE_ENV": "1"},
    )

    # When
    submit_candidates((candidate,), request, runner)

    # Then
    argv, environment = calls[0]
    assert argv[1:3] == (str(request.wiki_cli), "draft")
    assert "--authority" in argv and "advisory" in argv
    assert "--provenance" in argv and "observed" in argv
    assert "confirm" not in argv
    assert environment == {"SAFE_ENV": "1"}


def test_submit_candidates_does_not_invoke_runner_when_no_candidate(tmp_path: Path) -> None:
    # Given
    calls: list[tuple[str, ...]] = []

    def runner(argv: tuple[str, ...], _environment: dict[str, str]) -> CompletedProcess[str]:
        calls.append(argv)
        return CompletedProcess(argv, 0)

    request = WikiDraftRequest(tmp_path / "wiki_cli.py", "dm", {"SAFE_ENV": "1"})

    # When
    submit_candidates((), request, runner)

    # Then
    assert calls == []
