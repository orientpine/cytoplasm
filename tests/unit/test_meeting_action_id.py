from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "meeting"
sys.path.insert(0, str(SKILL / "scripts"))

import meeting_action_id  # noqa: E402


def test_ids_have_the_ten_character_project_year_sequence_shape():
    action_id = meeting_action_id.parse_id("HOGS260001")

    assert action_id.text == "HOGS260001"
    assert len(action_id.text) == meeting_action_id.ID_LEN == 10
    with pytest.raises(meeting_action_id.ActionIdError):
        meeting_action_id.parse_id("hogs260001")


def test_sequential_allocation_ignores_invalid_ids_and_advances_highest_match():
    action_id = meeting_action_id.next_id(
        "HOGS", 26, ("HOGS260001", "HOGS260008", "BAD", "ABCD260099")
    )

    assert action_id == meeting_action_id.ActionId("HOGS", 26, 9)


def test_project_codes_follow_hangul_and_ascii_candidates_without_duplicates():
    assert meeting_action_id.candidate_code("해양고신뢰성") == "HOGS"
    assert meeting_action_id.candidate_code("가") == "GXXX"
    assert list(meeting_action_id.alternates("해양고신뢰성"))[:4] == [
        "HOGS",
        "HOGR",
        "HOGA",
        "HOGB",
    ]


def test_registry_csv_round_trip_and_resolution_do_not_mutate_input():
    registry = {"HOGS": "해양고신뢰성", "SMTX": "SMT"}

    dumped = meeting_action_id.dump_registry(registry)
    code, updated = meeting_action_id.resolve_code("해양고신뢰성", registry)

    assert meeting_action_id.load_registry(dumped) == registry
    assert code == "HOGS"
    assert updated == registry
    assert updated is not registry
