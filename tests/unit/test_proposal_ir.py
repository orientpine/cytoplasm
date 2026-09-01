from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from skills.proposal.scripts.proposal_ir import (  # noqa: E402
    FigureSpec,
    PROFILES,
    SectionSpec,
    TableSpec,
    UnknownFigureToken,
    caption_numbers,
    check_caption_reference_consistency,
    figures_from_json,
    figures_to_json,
    main,
    referenced_numbers,
    resolve_figure_tokens,
    tables_from_json,
    tables_to_json,
)


def test_layout_profiles_match_budgets() -> None:
    thirty = PROFILES["30-page"].sections
    assert [s.target_pages for s in thirty] == [2, 8, 4, 12, 4]
    assert [s.figure_slots for s in thirty] == [1, 4, 2, 6, 2]
    assert [s.prose_char_budget for s in thirty] == [1350, 6000, 2800, 9000, 2800]
    ten = PROFILES["10-page"].sections
    assert [s.target_pages for s in ten] == [1, 2, 2, 3, 2]
    # Rescaled 2026-08-28: section 0 had no figure slot, so its band rendered no
    # prose, and the budgets ran 500 / 1000 / 750 / 1000 / 500 per page for one
    # document — refine enforces them, so correctly sized sections failed.
    assert [s.figure_slots for s in ten] == [1, 1, 1, 2, 1]
    assert [s.prose_char_budget for s in ten] == [900, 1800, 1800, 2700, 1800]


def test_resolve_tokens_in_document_order_and_unknown_is_typed() -> None:
    figs = (
        FigureSpec("fig-s1-01", "s1", (), "p", "c", "a" * 64, 0),
        FigureSpec("fig-s2-01", "s2", (), "p", "c", "b" * 64, 1),
    )
    resolved, mapping = resolve_figure_tokens("[[FIG:fig-s2-01]] [[FIG:fig-s2-01]]", figs)
    assert resolved == "그림 1 그림 1"
    assert mapping == {"fig-s2-01": 1, "fig-s1-01": 2}
    with pytest.raises(UnknownFigureToken, match="fig-s9-99"):
        resolve_figure_tokens("[[FIG:fig-s9-99]]", figs)


def test_caption_reference_consistency() -> None:
    figs = (FigureSpec("fig-s1-01", "s1", (), "p", "c", "a" * 64, 0),)
    text, mapping = resolve_figure_tokens("[[FIG:fig-s1-01]]", figs)
    assert check_caption_reference_consistency(text, mapping) == (True, "")
    assert referenced_numbers("그림 9") == {9}
    assert check_caption_reference_consistency("그림 9", mapping)[0] is False
    assert caption_numbers(figs, mapping) == {1}


def test_json_round_trip_and_no_timestamp() -> None:
    figs = (FigureSpec("fig-s1-01", "s1", ("c1",), "프롬프트", "캡션", "a" * 64, 0),)
    tables = (TableSpec("t1", "s1", "kpi", ("h",), (("v",),), ("c1",)),)
    assert figures_from_json(figures_to_json(figs)) == figs
    assert tables_from_json(tables_to_json(tables)) == tables
    assert "timestamp" not in figures_to_json(figs).lower()
    assert "timestamp" not in tables_to_json(tables).lower()


def test_section_id_must_be_in_range() -> None:
    with pytest.raises(ValueError):
        SectionSpec(9, 1, 1, 1)


def test_json_keys_are_sorted() -> None:
    figure = FigureSpec("fig-s1-01", "s1", (), "p", "c", "a" * 64, 0)
    output = figures_to_json((figure,))
    assert json.dumps(json.loads(output), sort_keys=True, indent=2, ensure_ascii=False) == output
    assert output.index('"band_index"') < output.index('"caption"') < output.index('"figure_id"')


def test_cli_contract(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    figures = tmp_path / "figures.json"
    text = tmp_path / "text.txt"
    figures.write_text(figures_to_json((FigureSpec("fig-s1-01", "s1", (), "p", "c", "a" * 64, 0),)), encoding="utf-8")
    text.write_text("[[FIG:fig-s9-99]]", encoding="utf-8")
    assert main(["resolve", "--figures", str(figures), "--text", str(text)]) == 3
    assert "UNKNOWN-FIGURE-TOKEN: fig-s9-99" in capsys.readouterr().err
    text.write_text("[[FIG:fig-s1-01]]", encoding="utf-8")
    assert main(["resolve", "--figures", str(figures), "--text", str(text)]) == 0
    assert "그림 1" in capsys.readouterr().out
    figures.write_text("{", encoding="utf-8")
    assert main(["resolve", "--figures", str(figures), "--text", str(text)]) == 2
    assert "INVALID-INPUT" in capsys.readouterr().err


def test_invalid_table_kind() -> None:
    with pytest.raises(ValueError):
        TableSpec("t", "s", "bad", (), (), ())



def test_layout_profiles_scale_budgets_and_always_allocate_a_figure() -> None:
    # Same two guards the engine carries: a section with no figure slot renders
    # no prose at all under the band renderer, and a profile whose chars-per-page
    # varies between sections makes refine reject correctly sized bodies.
    from skills.proposal.scripts.proposal_ir import PROFILES

    for profile in PROFILES.values():
        per_page = {
            spec.section_id: spec.prose_char_budget / spec.target_pages
            for spec in profile.sections
            if spec.target_pages
        }
        spread = max(per_page.values()) / min(per_page.values())
        assert spread <= 1.35, f"{profile.name} chars-per-page varies {spread:.2f}x: {per_page}"
        for spec in profile.sections:
            if spec.target_pages:
                assert spec.figure_slots >= 1, (
                    f"{profile.name} section {spec.section_id} allocates no figure"
                )
