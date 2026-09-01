"""Multi-sheet registry contract for the budget skill (과제별×년도별).

Legacy mode (no registry file) must stay byte-compatible with the single
``BUDGET_SHEET_ID`` behavior, so every failure here is fail-closed and loud.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "budget" / "scripts"))

import budget_gate  # noqa: E402
import budget_registry  # noqa: E402


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "sheets.json"
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return path


def _registry(projects: dict[str, object]) -> dict[str, object]:
    return {"version": 1, "projects": projects}


VALID = _registry({
    "무인굴착기": {"2026": "sheet-ex-2026", "2025": "sheet-ex-2025"},
    "autophagy": {"2026": "sheet-au-2026"},
})


def test_parse_returns_sorted_refs_with_stable_keys() -> None:
    refs = budget_registry.parse_registry(json.dumps(VALID, ensure_ascii=False))
    assert [(r.project, r.year, r.sheet_id) for r in refs] == [
        ("autophagy", 2026, "sheet-au-2026"),
        ("무인굴착기", 2025, "sheet-ex-2025"),
        ("무인굴착기", 2026, "sheet-ex-2026"),
    ]
    assert refs[0].sheet_key == "autophagy/2026"
    assert refs[1].sheet_key == "무인굴착기/2025"


@pytest.mark.parametrize(
    "payload",
    [
        "not json {",
        json.dumps({"version": 2, "projects": {"a": {"2026": "x"}}}),
        json.dumps({"version": 1}),
        json.dumps({"version": 1, "projects": {}}),
        json.dumps(_registry({"": {"2026": "x"}})),
        json.dumps(_registry({"a/b": {"2026": "x"}})),
        json.dumps(_registry({"a:b": {"2026": "x"}})),
        json.dumps(_registry({"a": {"26": "x"}})),
        json.dumps(_registry({"a": {"20x6": "x"}})),
        json.dumps(_registry({"a": {"2026": ""}})),
        json.dumps(_registry({"a": {"2026": "dup"}, "b": {"2027": "dup"}})),
        json.dumps(_registry({"a": "not-a-mapping"})),
    ],
)
def test_parse_rejects_malformed_registry_exit3(payload: str) -> None:
    with pytest.raises(budget_gate.GateError) as caught:
        budget_registry.parse_registry(payload)
    assert caught.value.exit_code == 3


def test_load_missing_file_means_legacy_mode(tmp_path: Path) -> None:
    assert budget_registry.load_registry(tmp_path / "absent.json") is None


def test_load_malformed_file_is_fail_closed(tmp_path: Path) -> None:
    path = _write(tmp_path, "not json {")
    with pytest.raises(budget_gate.GateError) as caught:
        budget_registry.load_registry(path)
    assert caught.value.exit_code == 3


def test_registry_path_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "custom.json"
    monkeypatch.setenv("BUDGET_SHEETS_FILE", str(override))
    assert budget_registry.registry_path() == override


def test_active_refs_without_registry_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BUDGET_SHEETS_FILE", str(tmp_path / "absent.json"))
    monkeypatch.setenv("BUDGET_SHEET_ID", "legacy-sheet")
    assert budget_registry.active_refs() is None


def test_active_refs_rejects_unlisted_legacy_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """레지스트리가 있는데 BUDGET_SHEET_ID가 미등재면 조용한 추적 누락 — 거부한다."""
    monkeypatch.setenv("BUDGET_SHEETS_FILE", str(_write(tmp_path, VALID)))
    monkeypatch.setenv("BUDGET_SHEET_ID", "unlisted-sheet")
    with pytest.raises(budget_gate.GateError) as caught:
        budget_registry.active_refs()
    assert caught.value.exit_code == 3
    assert "BUDGET_SHEET_ID" in str(caught.value)


def test_active_refs_allows_listed_legacy_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BUDGET_SHEETS_FILE", str(_write(tmp_path, VALID)))
    monkeypatch.setenv("BUDGET_SHEET_ID", "sheet-au-2026")
    refs = budget_registry.active_refs()
    assert refs is not None and len(refs) == 3


def test_select_single_ref_needs_no_flags() -> None:
    only = _registry({"autophagy": {"2026": "sheet-au-2026"}})
    refs = budget_registry.parse_registry(json.dumps(only))
    picked = budget_registry.select(refs, project="", year=0)
    assert picked.sheet_id == "sheet-au-2026"


def test_select_multiple_without_project_lists_known_exit2() -> None:
    refs = budget_registry.parse_registry(json.dumps(VALID, ensure_ascii=False))
    with pytest.raises(budget_gate.GateError) as caught:
        budget_registry.select(refs, project="", year=0)
    assert caught.value.exit_code == 2
    assert "무인굴착기" in str(caught.value) and "autophagy" in str(caught.value)


def test_select_project_defaults_to_latest_year() -> None:
    refs = budget_registry.parse_registry(json.dumps(VALID, ensure_ascii=False))
    picked = budget_registry.select(refs, project="무인굴착기", year=0)
    assert (picked.year, picked.sheet_id) == (2026, "sheet-ex-2026")


def test_select_explicit_year() -> None:
    refs = budget_registry.parse_registry(json.dumps(VALID, ensure_ascii=False))
    picked = budget_registry.select(refs, project="무인굴착기", year=2025)
    assert picked.sheet_id == "sheet-ex-2025"


def test_select_unknown_project_or_year_exit2() -> None:
    refs = budget_registry.parse_registry(json.dumps(VALID, ensure_ascii=False))
    for kwargs in ({"project": "없는과제", "year": 0}, {"project": "autophagy", "year": 1999}):
        with pytest.raises(budget_gate.GateError) as caught:
            budget_registry.select(refs, **kwargs)
        assert caught.value.exit_code == 2
