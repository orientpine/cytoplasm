"""Data-driven transcription regression harness for the personal-entity preflight.

Origin incident: a voice-transcribed request wrote a near-homophone of a
personal name into an external system. These fixtures lock that regression
class down as *data*: every case under ``fixtures/entity_preflight/`` is one
JSON file, and adding a future failure case is a matter of dropping in one more
file — :func:`test_adding_one_fixture_file_adds_one_case` proves discovery is a
directory listing rather than a hand-maintained list.

The harness drives the published entry points only — ``resolver.detect_entities``
for the detection target, ``resolver.resolve_preflight`` for the decision, and
``clarify.render_clarify`` / ``audit.operational_event`` for the owner-facing and
operational surfaces. Candidates are supplied through the published
``CandidateResolver`` protocol, so swapping a fixture adapter for the production
personal-RAG, memory, or address-book adapter changes no fixture and no
assertion. Nothing here asserts an implementation detail such as a score, a
candidate ordering, or a rewritten query string, so the cases stay valid as the
resolver evolves.

Nothing here is skipped or xfailed: a missing published entry point, a missing
fixture directory, or a self-contradictory fixture fails loudly.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Final

import pytest
from entity_preflight_fixtures import (
    FixtureCase,
    discover_cases,
    fixture_paths,
    fixture_sources,
)

from automation.entity_preflight.audit import input_sha256, operational_event
from automation.entity_preflight.clarify import ENTITY_CLARIFY_MARKER, render_clarify
from automation.entity_preflight.contracts import AuditMetadata, PreflightDecision
from automation.entity_preflight.policy import POLICY_SEED_PATH, load_policy
from automation.entity_preflight.resolver import detect_entities, resolve_preflight

_POLICY: Final = load_policy(POLICY_SEED_PATH)
_CASES: Final = discover_cases()
_CASE_IDS: Final = [case.case_id for case in _CASES]
_CONFIRMATION_CASES: Final = [case for case in _CASES if case.expected.needs_confirmation]
_CONFIRMATION_IDS: Final = [case.case_id for case in _CONFIRMATION_CASES]

_REQUIRED_CATEGORIES: Final = frozenset(
    {
        "exact_spelling",
        "spelling_variant",
        "spacing_variant",
        "homophone_transcription",
        "relationship_family",
        "relationship_colleague",
        "shared_name_homonym",
        "no_candidate",
    }
)

_PERSONAL_DATA_PATTERNS: Final = {
    "email address": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "phone number": re.compile(r"\d{2,4}-\d{3,4}-\d{4}"),
    "bare phone number": re.compile(r"\b0\d{9,10}\b"),
    "network address": re.compile(r"(?:https?|mailto|tel):"),
    "long digit run": re.compile(r"\d{6,}"),
}


def _decide(case: FixtureCase) -> PreflightDecision:
    audit = AuditMetadata(
        correlation_id=f"corr-{case.case_id}",
        policy_version=_POLICY.version,
        requested_at="2026-07-28T00:00:00Z",
        actor="owner",
        purpose="external_write_preflight",
        input_sha256=input_sha256(case.request.raw_text),
        sensitive_audit_ref=f"private://fixture/audit/{case.case_id}",
    )
    return resolve_preflight(case.request, audit, fixture_sources(case))


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_resolver_collects_every_declared_candidate(case: FixtureCase) -> None:
    """Given a fixture, when the resolver runs over the published source protocol,
    then the decision carries every declared candidate exactly once."""
    decision = _decide(case)

    collected = sorted(candidate.candidate_id for candidate in decision.candidates)
    assert collected == sorted(candidate.candidate_id for candidate in case.candidates)


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_detection_finds_the_declared_target(case: FixtureCase) -> None:
    """Given the fixture's input text and entity hints, when detection runs,
    then it returns exactly the declared target spans."""
    hints = {entity.surface: entity.entity_kind for entity in case.request.entities}

    detected = detect_entities(case.request.raw_text, hints)

    assert [(item.surface, item.entity_kind, item.start, item.end) for item in detected] == [
        (item.surface, item.entity_kind, item.start, item.end) for item in case.request.entities
    ]


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_decision_matches_fixture(case: FixtureCase) -> None:
    """Given a transcription case, when the preflight decides,
    then the decision, reason, and confirmation need match the fixture."""
    decision = _decide(case)

    assert (decision.decision, decision.reason, decision.needs_confirmation) == (
        case.expected.decision,
        case.expected.reason,
        case.expected.needs_confirmation,
    )


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_auto_normalization_matches_fixture(case: FixtureCase) -> None:
    """Given a transcription case, when the preflight decides,
    then exactly the declared mentions carry the declared normalized values."""
    decision = _decide(case)

    selected = {value.mention_id: value.normalized_value for value in decision.selected}
    assert selected == dict(case.expected.selected)
    assert bool(selected) == case.expected.auto_normalization


@pytest.mark.parametrize("case", _CONFIRMATION_CASES, ids=_CONFIRMATION_IDS)
def test_clarify_names_every_unresolved_mention(case: FixtureCase) -> None:
    """Given an unresolved case, when the clarify turn is rendered,
    then it is marked ENTITY-CLARIFY and names each declared mention surface."""
    decision = _decide(case)
    rendered = render_clarify(decision)

    surfaces = {
        entity.surface
        for entity in case.request.entities
        if entity.mention_id in case.expected.clarify_mentions
    }
    assert rendered.startswith(ENTITY_CLARIFY_MARKER)
    assert all(surface in rendered for surface in surfaces)
    assert case.request.raw_text not in rendered
    assert all(candidate.source_ref not in rendered for candidate in case.candidates)


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_operational_event_carries_no_personal_value(case: FixtureCase) -> None:
    """Given any case, when the redacted operational event is built,
    then no surface, normalized value, or display value appears in it."""
    event = json.dumps(operational_event(_decide(case)), ensure_ascii=False)

    personal = {entity.surface for entity in case.request.entities}
    personal.update(candidate.normalized_value for candidate in case.candidates)
    personal.update(candidate.display_value for candidate in case.candidates)
    assert all(value not in event for value in personal)


def test_discovered_case_count_equals_fixture_file_count() -> None:
    """Given the fixture directory, when cases are discovered,
    then there is exactly one parametrized case per file, keyed by file name."""
    paths = fixture_paths()

    assert len(_CASES) == len(paths)
    assert _CASE_IDS == [path.stem for path in paths]


def test_adding_one_fixture_file_adds_one_case(tmp_path: Path) -> None:
    """Given a copy of the fixture directory, when one more file is dropped in,
    then discovery reports exactly one more case without any code change."""
    staged = tmp_path / "entity_preflight"
    staged.mkdir()
    for path in fixture_paths():
        shutil.copy(path, staged / path.name)
    before = len(discover_cases(staged))
    template = json.loads((staged / "exact-spelling-auto.json").read_text(encoding="utf-8"))
    template["case_id"] = "dropped-in-regression"
    template["request"]["request_id"] = "ef3-req-dropped-in"
    (staged / "dropped-in-regression.json").write_text(
        json.dumps(template, ensure_ascii=False), encoding="utf-8"
    )

    after = discover_cases(staged)

    assert len(after) == before + 1
    assert "dropped-in-regression" in {case.case_id for case in after}


def test_required_regression_categories_are_covered() -> None:
    """Given the fixture set, when categories are collected,
    then every required transcription regression class has at least one case."""
    covered = {case.category for case in _CASES}

    assert _REQUIRED_CATEGORIES <= covered


@pytest.mark.parametrize("path", fixture_paths(), ids=[p.stem for p in fixture_paths()])
def test_fixture_carries_no_structural_personal_data(path: Path) -> None:
    """Given a fixture file, when it is scanned for real contact-data shapes,
    then none is present — every name in the harness is invented."""
    raw = path.read_text(encoding="utf-8")

    found = {label for label, pattern in _PERSONAL_DATA_PATTERNS.items() if pattern.search(raw)}
    assert not found, f"{path.name} looks like it carries real data: {sorted(found)}"
