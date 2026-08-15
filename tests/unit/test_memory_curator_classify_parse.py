from __future__ import annotations

import json

import pytest

from automation.memory_curator.classify_model import EntryVerdict
from automation.memory_curator.classify_parse import parse_verdict

_ENTRY = "Prefer deterministic routing for durable policy decisions."


def _reply(
    *,
    route: str = "TWIN",
    evidence: str = "deterministic routing",
    reason: str = "durable policy",
) -> str:
    return json.dumps({"route": route, "evidence": evidence, "reason": reason})


def _assert_parse_closed(raw: str, entry_text: str = _ENTRY) -> None:
    verdict = parse_verdict(raw, entry_text, source_kind="memory")

    assert verdict == EntryVerdict(
        source_kind="memory",
        entry_text=entry_text,
        route="UNCERTAIN",
        evidence="",
        reason="",
        veto="parse",
        llm_called=True,
    )


def test_valid_object_returns_grounded_verdict() -> None:
    # Given a strict JSON reply whose evidence occurs in the entry
    raw = _reply(reason="durable policy")

    # When the untrusted reply is parsed
    verdict = parse_verdict(raw, _ENTRY, source_kind="memory")

    # Then the grounded values are returned and the LLM call is recorded.
    assert verdict == EntryVerdict(
        source_kind="memory",
        entry_text=_ENTRY,
        route="TWIN",
        evidence="deterministic routing",
        reason="durable policy",
        veto=None,
        llm_called=True,
    )


@pytest.mark.parametrize("opening_fence", ["```", "```json"])
def test_single_markdown_fence_is_removed(opening_fence: str) -> None:
    # Given one supported pair of Markdown fence lines
    raw = f"{opening_fence}\n{_reply()}\n```"

    # When the fenced reply is parsed
    verdict = parse_verdict(raw, _ENTRY, source_kind="user")

    # Then only the fences are ignored and the object is accepted.
    assert verdict.route == "TWIN"
    assert verdict.evidence == "deterministic routing"
    assert verdict.veto is None
    assert verdict.llm_called is True


@pytest.mark.parametrize(
    "raw",
    [
        f"Here is the verdict: {_reply()}",
        f"{_reply()} This is final.",
        f"```json\n{_reply()}\n```\nThis is final.",
    ],
)
def test_prose_outside_json_closes_parse(raw: str) -> None:
    # Given prose before or after an otherwise valid JSON object
    # When the reply is parsed / Then authorization closes.
    _assert_parse_closed(raw)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "route": "TWIN",
            "evidence": "deterministic routing",
            "reason": "durable policy",
            "confidence": 0.99,
        },
        {"route": "TWIN", "evidence": "deterministic routing"},
    ],
)
def test_non_exact_key_set_closes_parse(payload: dict[str, str | float]) -> None:
    # Given an object with an extra or missing key
    # When the reply is parsed / Then authorization closes.
    _assert_parse_closed(json.dumps(payload))


@pytest.mark.parametrize("route", ["FOO", "twin"])
def test_unknown_or_wrong_case_route_closes_parse(route: str) -> None:
    # Given a route outside the case-sensitive contract
    # When the reply is parsed / Then authorization closes.
    _assert_parse_closed(_reply(route=route))


def test_evidence_absent_from_entry_closes_parse() -> None:
    # Given sufficiently long evidence that is not grounded in the entry
    # When the reply is parsed / Then authorization closes.
    _assert_parse_closed(_reply(evidence="unrelated evidence"))


def test_seven_character_evidence_closes_parse() -> None:
    # Given grounded evidence below the eight-character floor
    # When the reply is parsed / Then authorization closes.
    _assert_parse_closed(_reply(evidence="abcdefg"), "Contains abcdefg here.")


def test_exactly_eight_character_evidence_is_accepted() -> None:
    # Given grounded evidence exactly at the eight-character floor
    entry = "Contains abcdefgh here."

    # When the reply is parsed
    verdict = parse_verdict(
        _reply(evidence="abcdefgh"), entry, source_kind="memory"
    )

    # Then the evidence is accepted.
    assert verdict.evidence == "abcdefgh"
    assert verdict.veto is None


def test_whitespace_collapsed_evidence_is_accepted() -> None:
    # Given evidence whose whitespace differs from the entry substring
    entry = "Prefer durable routing for every policy."

    # When the reply is parsed
    verdict = parse_verdict(
        _reply(evidence="durable\n   routing"), entry, source_kind="memory"
    )

    # Then collapsed literal containment grounds the original evidence.
    assert verdict.evidence == "durable\n   routing"
    assert verdict.veto is None


@pytest.mark.parametrize("raw", ["not JSON", "[]", "42", '"scalar"'])
def test_non_object_json_closes_parse(raw: str) -> None:
    # Given malformed JSON or a valid non-object JSON value
    # When the reply is parsed / Then authorization closes.
    _assert_parse_closed(raw)


def test_reason_is_truncated_to_two_hundred_characters() -> None:
    # Given a valid reply with a 300-character reason
    raw = _reply(reason="r" * 300)

    # When the reply is parsed
    verdict = parse_verdict(raw, _ENTRY, source_kind="memory")

    # Then reason truncation alone does not reject the verdict.
    assert verdict.reason == "r" * 200
    assert verdict.veto is None


@pytest.mark.parametrize(
    "payload",
    [
        {"route": 1, "evidence": "deterministic routing", "reason": "valid"},
        {"route": "TWIN", "evidence": 1, "reason": "valid"},
        {"route": "TWIN", "evidence": "deterministic routing", "reason": 1},
    ],
)
def test_wrong_field_type_closes_parse(payload: dict[str, str | int]) -> None:
    # Given a route, evidence, or reason with the wrong JSON type
    # When the reply is parsed / Then authorization closes.
    _assert_parse_closed(json.dumps(payload))
