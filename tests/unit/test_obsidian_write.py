from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from automation.obsidian_write import ObsidianWriteError, load_config, plan_note


def test_plan_note_is_deterministic_and_places_personal_content_in_para() -> None:
    # Given
    title = "  연구  계획 / 초안  "
    body = "첫 번째 실험을 준비한다."

    # When
    first = plan_note(title, body, institutional=False, bucket_hint="project")
    second = plan_note(title, body, institutional=False, bucket_hint="Project")

    # Then
    assert first == second
    assert first.relpath.parent == PurePosixPath("000_PARA/Project")
    assert first.relpath.suffix == ".md"
    assert "/" not in first.relpath.name


def test_plan_note_places_institutional_content_in_kimm_para() -> None:
    # Given / When
    plan = plan_note("기관 회의", "회의 요약", institutional=True, bucket_hint="resource")

    # Then
    assert plan.relpath.parent == PurePosixPath("001_KIMM_PARA/Resource")


def test_plan_note_routes_unclassifiable_content_to_existing_inbox() -> None:
    # Given / When
    plan = plan_note("떠오른 생각", "정리가 필요하다.", institutional=False, bucket_hint=None)

    # Then
    assert plan.relpath.parent == PurePosixPath("000_PARA/Area/000_정리되지않은생각들")


def test_load_config_refuses_missing_configuration(tmp_path: Path) -> None:
    # Given / When / Then
    with pytest.raises(ObsidianWriteError, match="configuration") as captured:
        _ = load_config(tmp_path / "missing.json")
    assert captured.value.retryable is False
