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


_CONFIG_TEMPLATE = """{{
  "repo_url": "git@example.invalid:owner/vault.git",
  "clone_dir": "{clone_dir}",
  "ssh_key_path": "{key_path}",
  "branch": "main"
}}"""


def _provisioned_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.json"
    _ = path.write_text(
        _CONFIG_TEMPLATE.format(clone_dir=tmp_path / "clone", key_path=tmp_path / "key"),
        encoding="utf-8",
    )
    return path


def test_load_config_defaults_the_fetch_timeout_to_fifteen_minutes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: no override provisioned.
    monkeypatch.delenv("OBSIDIAN_WRITE_FETCH_TIMEOUT", raising=False)

    # When / Then: 120 s killed every real fetch; the default must clear a cold pack.
    assert load_config(_provisioned_config(tmp_path)).fetch_timeout_seconds == 900.0


def test_load_config_honours_the_fetch_timeout_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setenv("OBSIDIAN_WRITE_FETCH_TIMEOUT", "1800")

    # When / Then
    assert load_config(_provisioned_config(tmp_path)).fetch_timeout_seconds == 1800.0


@pytest.mark.parametrize("override", ("0", "-1", "abc", ""))
def test_load_config_refuses_an_unusable_fetch_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    override: str,
) -> None:
    # Given
    monkeypatch.setenv("OBSIDIAN_WRITE_FETCH_TIMEOUT", override)

    # When / Then: a bad override must fail loudly, not silently restore the 120 s bug.
    with pytest.raises(ObsidianWriteError, match="fetch timeout") as captured:
        _ = load_config(_provisioned_config(tmp_path))
    assert captured.value.retryable is False


# ---- frontmatter hoist (2026-09-04, plaud lifelog v2): body 선두 '---' 블록은 제목 위로, callout 은 생략 ----


def test_render_note_hoists_a_leading_frontmatter_block_above_the_title_and_drops_the_callout() -> None:
    from pathlib import PurePosixPath

    from automation.obsidian_write.note import NotePlan, render_note

    frontmatter = (
        "---\ntags: [lifelog]\ntitle: \"제목: 하나\"\nsource: PLAUD 녹음 x\n"
        "created: 2026-09-02T09:02:00\nmodified: 2026-09-02T09:02:00\n---"
    )
    plan = NotePlan(
        PurePosixPath("000_PARA/Area/Lifelog/2026/x.md"),
        "제목: 하나",
        frontmatter + "\n\n## 한눈에\n\n- 녹음:: x",
    )

    rendered = render_note(plan, created="2026-09-04", modified="2026-09-04")

    assert rendered == frontmatter + "\n\n# 제목: 하나\n\n## 한눈에\n\n- 녹음:: x\n"
    assert ">[!info]" not in rendered


def test_render_note_without_frontmatter_keeps_the_callout_layout_byte_for_byte() -> None:
    from pathlib import PurePosixPath

    from automation.obsidian_write.note import NotePlan, render_note

    plan = NotePlan(PurePosixPath("000_PARA/Resource/n--abc.md"), "제목", "본문")

    assert render_note(plan, created="2026-09-04", modified="2026-09-05") == (
        "# 제목\n\n>[!info]\n> Author: cha\n> Created: 2026-09-04\n> Modified: 2026-09-05\n"
        "> Location: 000_PARA/Resource\n> Tag: #personal\n\n본문\n"
    )


def test_render_note_treats_an_unclosed_leading_rule_as_body_not_frontmatter() -> None:
    from pathlib import PurePosixPath

    from automation.obsidian_write.note import NotePlan, render_note

    plan = NotePlan(PurePosixPath("000_PARA/Resource/n--abc.md"), "제목", "---\n구분선으로 시작하는 본문")

    rendered = render_note(plan, created="2026-09-04", modified="2026-09-04")

    assert rendered.startswith("# 제목\n\n>[!info]\n")
    assert rendered.endswith("\n\n---\n구분선으로 시작하는 본문\n")
