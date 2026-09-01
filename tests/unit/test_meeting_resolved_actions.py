from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "meeting"
sys.path.insert(0, str(SKILL / "scripts"))

import meeting_llm  # noqa: E402


def test_resolved_actions_object_and_bare_string_forms_parse():
    extraction = meeting_llm.parse_extraction(
        '{"resolved_actions": [{"id": " ma-7 ", "basis": "확인"}, "  ma-8  "]}'
    )
    assert extraction.resolved_actions == (
        meeting_llm.ResolvedAction("MA-7", "확인"),
        meeting_llm.ResolvedAction("MA-8"),
    )


def test_resolved_actions_drop_entries_without_id_and_malformed_values():
    assert meeting_llm.parse_extraction(
        '{"resolved_actions": [{"basis": "x"}, {"id": " "}, 3]}'
    ).resolved_actions == ()
    assert meeting_llm.parse_extraction('{"resolved_actions": "bad"}').resolved_actions == ()


def test_absent_and_v4_payloads_remain_compatible():
    extraction = meeting_llm.parse_extraction(
        '{"decisions": ["d"], "todos": [{"title": "t", "basis": "b"}]}'
    )
    assert extraction.resolved_actions == ()
    assert extraction.decisions[0].text == "d"
    assert extraction.todos[0].title == "t"


def test_build_prompt_substitutes_open_actions_and_blanks_when_empty():
    template = "{{MEETING_TEXT}} {{MY_NAMES}} {{OPEN_ACTIONS}}"
    assert meeting_llm.build_prompt(
        template, meeting_text="m", my_names="n", open_actions="MA-1"
    ) == "m n MA-1"
    assert meeting_llm.build_prompt(template, meeting_text="m", my_names="n") == "m n "


def test_v5_prompt_has_required_material_and_one_marker():
    path = SKILL / "prompts" / "meeting-extraction-v5.md"
    text = path.read_text(encoding="utf-8")
    assert text.splitlines().count("<<<PROMPT>>>") == 1
    body = meeting_llm.load_prompt_template(path)
    for placeholder in ("{{MEETING_TEXT}}", "{{MY_NAMES}}", "{{SLIDES}}", "{{OPEN_ACTIONS}}"):
        assert placeholder in body
