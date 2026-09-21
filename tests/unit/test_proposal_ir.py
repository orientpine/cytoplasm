from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
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


@pytest.fixture
def mounted_proposal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Copy the skill into the governed release/symlink layout, without a checkout."""
    release = tmp_path / "releases" / "proposal" / ("a" * 64)
    _ = shutil.copytree(
        ROOT / "skills" / "proposal", release,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    live = tmp_path / "live" / "proposal"
    live.parent.mkdir()
    live.symlink_to(release, target_is_directory=True)
    monkeypatch.delenv("AUTOPHAGY_REPO_ROOT", raising=False)
    return live


@pytest.mark.parametrize("entry", ["proposal_cli.py", "proposal_ir.py"])
def test_entry_point_runs_without_checkout_package(
    mounted_proposal: Path, entry: str,
) -> None:
    # Given: the mounted skill has no enclosing skills package.
    command = mounted_proposal / "scripts" / entry
    # When: its real file entry point runs with no ambient Python search path.
    result = subprocess.run(
        [sys.executable, "-I", str(command), "--help"],
        cwd=mounted_proposal.parent, capture_output=True, text=True,
        timeout=30, check=False,
    )
    # Then: importing the public profile cannot prevent the CLI from starting.
    assert result.returncode == 0, result.stderr


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


def test_profile_values_have_no_second_definition_in_script_modules() -> None:
    # Given: one public contract owns every machine-consumed profile value.
    from skills.proposal.layout_profile import LAYOUT_PROFILES

    vectors = {
        tuple(values.values())
        for profile in LAYOUT_PROFILES.values()
        for values in (
            profile.section_page_targets, profile.figure_targets, profile.prose_budgets
        )
    }
    duplicates: list[str] = []

    # When: Python modules and Python heredoc fixtures under scripts are inspected.
    scripts = ROOT / "skills/proposal/scripts"
    sources = [(path, path.read_text(encoding="utf-8")) for path in scripts.rglob("*.py")]
    sources.extend(
        (path, match.group(1))
        for path in scripts.rglob("*.sh")
        for match in re.finditer(r"<<'PY'\n(.*?)\nPY", path.read_text(encoding="utf-8"), re.S)
    )
    for path, source in sources:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            match node:
                case ast.Tuple(elts=values) | ast.List(elts=values):
                    elements = values
                case ast.Dict(values=values):
                    elements = values
                case _:
                    continue
            if all(isinstance(value, ast.Constant) for value in elements):
                literal = tuple(ast.literal_eval(value) for value in elements)
                if literal in vectors:
                    duplicates.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    # Then: no script embeds a second profile vector, even under another name.
    assert duplicates == []


def test_profile_adapter_tracks_the_contract_when_values_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a distinct shared profile, not the current fallback values.
    import importlib.util
    from dataclasses import replace
    from skills.proposal import layout_profile

    changed = replace(
        layout_profile.LAYOUT_PROFILES["10-page"],
        section_page_targets={0: 7}, figure_targets={0: 3}, prose_budgets={0: 4321},
    )
    monkeypatch.setattr(layout_profile, "LAYOUT_PROFILES", {changed.name: changed})
    spec = importlib.util.spec_from_file_location(
        "skills.proposal.scripts.proposal_ir", ROOT / "skills/proposal/scripts/proposal_ir.py"
    )
    assert spec is not None and spec.loader is not None
    adapter = importlib.util.module_from_spec(spec)

    # When: the real adapter is loaded against the changed shared definition.
    spec.loader.exec_module(adapter)

    # Then: both the profile set and its values come from that definition.
    assert list(adapter.PROFILES) == ["10-page"]
    section = adapter.PROFILES["10-page"].sections[0]
    assert (section.section_id, section.target_pages, section.figure_slots,
            section.prose_char_budget) == (0, 7, 3, 4321)


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


@pytest.mark.parametrize(("figure_id", "exit_code"), [("fig-s1-01", 0), ("fig-s9-99", 3)])
def test_direct_cli_resolves_or_refuses_when_run_outside_checkout(
    tmp_path: Path, figure_id: str, exit_code: int,
) -> None:
    # Given: local input files and no checkout on the interpreter search path.
    figures = tmp_path / "figures.json"
    figures.write_text(
        figures_to_json((FigureSpec("fig-s1-01", "s1", (), "p", "c", "a" * 64, 0),)),
        encoding="utf-8",
    )
    text = tmp_path / "text.txt"
    text.write_text(f"[[FIG:{figure_id}]]", encoding="utf-8")

    # When: the public file entry point runs in an isolated interpreter.
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "skills/proposal/scripts/proposal_ir.py"),
         "resolve", "--figures", str(figures), "--text", str(text)],
        cwd=tmp_path, capture_output=True, text=True, timeout=10, check=False,
    )

    # Then: document numbering or the typed refusal survives the engine import.
    assert result.returncode == exit_code, result.stderr
    if exit_code == 0:
        assert result.stdout == "그림 1"
    else:
        assert f"UNKNOWN-FIGURE-TOKEN: {figure_id}" in result.stderr


def test_ir_cli_runs_when_the_private_engine_is_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the public proposal tree without the manifest-excluded engine.
    exported = tmp_path / "export" / "skills" / "proposal"
    _ = shutil.copytree(
        ROOT / "skills/proposal", exported,
        ignore=shutil.ignore_patterns("engine", "__pycache__"),
    )
    monkeypatch.delenv("AUTOPHAGY_REPO_ROOT", raising=False)
    figures = tmp_path / "figures.json"
    _ = figures.write_text(
        figures_to_json((FigureSpec("fig-s1-01", "s1", (), "p", "c", "a" * 64, 0),)),
        encoding="utf-8",
    )
    text = tmp_path / "body.txt"
    _ = text.write_text("[[FIG:fig-s1-01]]", encoding="utf-8")

    # When: the isolated interpreter executes the exported CLI, not this checkout.
    result = subprocess.run(
        [sys.executable, "-I", str(exported / "scripts/proposal_ir.py"),
         "resolve", "--figures", str(figures), "--text", str(text)],
        cwd=tmp_path, capture_output=True, text=True, timeout=10, check=False,
    )

    # Then: the public runtime can resolve a figure without the private engine.
    assert result.returncode == 0, result.stderr
    assert result.stdout == "그림 1"


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
