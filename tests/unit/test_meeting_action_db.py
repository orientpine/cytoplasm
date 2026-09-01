from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "meeting"
sys.path.insert(0, str(SKILL / "scripts"))

import meeting_action_db  # noqa: E402


def test_idempotent_reingest_does_not_create_the_same_meeting_item_twice():
    kwargs = {
        "project": "해양고신뢰성",
        "code": "HOGS",
        "year": 26,
        "new_items": (meeting_action_db.NewItem("  센서  검증 ", "차"),),
        "resolved_ids": (),
        "note_name": "2026-08-27.md",
        "on": date(2026, 8, 27),
    }

    first = meeting_action_db.merge((), **kwargs)
    second = meeting_action_db.merge(first.records, **kwargs)

    assert [record.id for record in first.created] == ["HOGS260001"]
    assert second.created == ()
    assert len(second.records) == 1


def test_closing_an_unknown_id_is_ignored_without_inventing_a_record():
    existing = meeting_action_db.Record(
        "HOGS260001", "해양", "센서 검증", "차", "", "open", "2026-08-01", "old.md", "", "", ""
    )

    result = meeting_action_db.merge(
        (existing,),
        project="해양",
        code="HOGS",
        year=26,
        new_items=(),
        resolved_ids=("HOGS260999",),
        note_name="new.md",
        on=date(2026, 8, 27),
    )

    assert result.records == (existing,)
    assert result.closed == ()
    assert result.outstanding == (existing,)


def test_database_csv_round_trip_is_canonical():
    records = (
        meeting_action_db.Record(
            "HOGS260002", "해양", "둘", "", "", "open", "2026-08-02", "b.md", "", "", ""
        ),
        meeting_action_db.Record(
            "HOGS260001", "해양", "하나", "차", "2026-08-20", "done", "2026-08-01", "a.md", "2026-08-03", "a.md", "근거"
        ),
    )

    dumped = meeting_action_db.dump(records)

    assert meeting_action_db.dump(meeting_action_db.load(dumped)) == dumped


def test_rendered_sections_escape_table_cells_and_use_empty_marker():
    record = meeting_action_db.Record(
        "HOGS260001", "해양", "A|B", "차", "", "open", "", "", "", "", ""
    )

    sections = meeting_action_db.render_sections(outstanding=(), created=(record,))

    assert sections[4] == "- 없음"
    assert "| HOGS260001 | A\\|B | — | 차 |" in sections
