"""Derive immutable proposal versions from collected delta snapshots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

from skills.proposal.scripts import proposal_cli, proposal_delta, proposal_improve_cmd
from skills.proposal.scripts.proposal_images import fake_png
from skills.proposal.scripts.proposal_ir import FigureSpec, figures_to_json
from skills.proposal.scripts.proposal_version import Staging, VersionStore


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_object(path: Path) -> dict[str, object]:
    value = cast(object, json.loads(path.read_text(encoding="utf-8")))
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _parent(
    root: Path,
    *,
    assertions: dict[str, object] | None = None,
    figures: tuple[FigureSpec, ...] = (),
) -> tuple[VersionStore, Path]:
    store = VersionStore(root)
    staging = store.begin("demo", "a" * 64)
    assert isinstance(staging, Staging)
    (staging.path / "inputs" / "brief.md").write_text("seed inputs\n", encoding="utf-8")
    (staging.path / "corpus" / "public.md").write_text(
        "seed corpus\n", encoding="utf-8"
    )
    (staging.path / "out" / "section-1.marker").write_bytes(b"section-one")
    (staging.path / "out" / "section-3.marker").write_bytes(b"section-three")
    if figures:
        (staging.path / "figures.json").write_text(
            figures_to_json(figures) + "\n", encoding="utf-8"
        )
        for figure in figures:
            (staging.path / "images" / f"{figure.figure_id}.png").write_bytes(
                fake_png(figure.figure_id)
            )
    manifest: dict[str, object] = {
        "parent": None,
        "request": {
            "pins": {"engine": "test-pin"},
            "profile": "30-page",
            "template_sha256": "seed-template",
        },
        "schema_version": 1,
    }
    if assertions is not None:
        manifest["assertions"] = assertions
    version = store.promote("demo", staging, manifest)
    assert version == "v000001"
    return store, root / "demo" / "versions" / version


def _collect(
    root: Path,
    sources: tuple[proposal_delta.DeltaSource, ...],
    *,
    marker: str = "b",
) -> proposal_delta.DeltaReport:
    destination = root / "demo" / "staging" / (marker * 64)
    return proposal_delta.collect_deltas(
        "demo", since_version="v000001", dest_dir=destination, sources=sources
    )


def _background_source() -> proposal_delta.DeltaSource:
    return proposal_delta.DeltaSource(
        "meeting",
        "note:meetings/background.md",
        "배경 및 선행연구 추가: 공개 문헌의 비교 근거를 반영한다.\n".encode(),
    )


def _kpi_source(source_key: str, value: str) -> proposal_delta.DeltaSource:
    return proposal_delta.DeltaSource(
        "meeting", source_key, f"KPI 굴착 오차: {value}\n".encode()
    )


def test_background_delta_regenerates_only_section_one_and_prior_research_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROPOSAL_ROOT", str(tmp_path))
    _store, parent = _parent(tmp_path)
    section_three_sha = _sha256((parent / "out" / "section-3.marker").read_bytes())
    report = _collect(tmp_path, (_background_source(),))

    result = proposal_improve_cmd.improve_from_report(
        "demo", since_version="v000001", report=report
    )

    assert result.plan.sections == frozenset({1})
    assert result.plan.tables == frozenset({"prior-research"})
    assert result.plan.figures == frozenset()
    child = tmp_path / "demo" / "versions" / result.version
    assert not (child / "out" / "section-1.marker").exists()
    assert (
        _sha256((child / "out" / "section-3.marker").read_bytes()) == section_three_sha
    )
    manifest = _json_object(child / "manifest.json")
    assert cast(dict[str, object], manifest["regeneration_plan"])["sections"] == [1]


def test_unchanged_figure_evidence_reuses_png_and_recorded_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROPOSAL_ROOT", str(tmp_path))
    png = fake_png("fig-s3-01")
    png_sha = _sha256(png)
    figure = FigureSpec(
        "fig-s3-01",
        "s3",
        ("public:claim-1",),
        "public excavator method",
        "수행 방법 개념도",
        png_sha,
        0,
    )
    _store, parent = _parent(tmp_path, figures=(figure,))
    assert (parent / "images" / "fig-s3-01.png").read_bytes() == png
    report = _collect(tmp_path, (_background_source(),))

    result = proposal_improve_cmd.improve_from_report(
        "demo", since_version="v000001", report=report
    )

    child = tmp_path / "demo" / "versions" / result.version
    records = cast(
        list[dict[str, object]], json.loads((child / "figures.json").read_text())
    )
    assert result.reused_figures == 1
    assert records[0]["png_sha256"] == png_sha
    assert _sha256((child / "images" / "fig-s3-01.png").read_bytes()) == png_sha


def test_conflicting_kpis_abort_then_index_supersedes_records_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROPOSAL_ROOT", str(tmp_path))
    store, _parent_path = _parent(tmp_path)
    first = _kpi_source("note:meetings/kpi-a.md", "10 mm")
    second = _kpi_source("note:meetings/kpi-b.md", "20 mm")
    report = _collect(tmp_path, (first, second), marker="c")
    before = sorted(path.name for path in (tmp_path / "demo" / "versions").iterdir())

    with pytest.raises(proposal_improve_cmd.DeltaConflictUnresolved) as raised:
        proposal_improve_cmd.improve_from_report(
            "demo", since_version="v000001", report=report
        )

    assert raised.value.exit_code == proposal_improve_cmd.DELTA_CONFLICT_UNRESOLVED_EXIT
    assert first.source_key in str(raised.value)
    assert second.source_key in str(raised.value)
    assert "10 mm" in str(raised.value)
    assert "20 mm" in str(raised.value)
    assert (
        sorted(path.name for path in (tmp_path / "demo" / "versions").iterdir())
        == before
    )
    assert store.head("demo") == "v000001"
    index_path = report.destination / "delta" / "INDEX.json"
    index = cast(
        list[dict[str, object]], json.loads(index_path.read_text(encoding="utf-8"))
    )
    target = next(entry for entry in index if entry["source_key"] == second.source_key)
    target["supersedes"] = first.source_key
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    result = proposal_improve_cmd.improve_from_report(
        "demo", since_version="v000001", report=report
    )

    assert result.version == "v000002"
    assert result.resolved_conflicts
    changelog = cast(
        list[dict[str, object]],
        json.loads((tmp_path / "demo" / "changelog.json").read_text(encoding="utf-8")),
    )
    assert changelog[-1]["conflicts_resolved"]


def test_reversed_index_supersedes_has_distinct_run_key_and_reports_advanced_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROPOSAL_ROOT", str(tmp_path))
    _parent(tmp_path)
    first = _kpi_source("note:meetings/kpi-a.md", "10 mm")
    second = _kpi_source("note:meetings/kpi-b.md", "20 mm")
    report = _collect(tmp_path, (first, second), marker="6")
    index_path = report.destination / "delta" / "INDEX.json"
    index = cast(
        list[dict[str, object]], json.loads(index_path.read_text(encoding="utf-8"))
    )
    by_source = {cast(str, entry["source_key"]): entry for entry in index}
    by_source[second.source_key]["supersedes"] = first.source_key
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    first_result = proposal_improve_cmd.improve_from_report(
        "demo", since_version="v000001", report=report
    )
    first_run_key = first_result.run_key
    assert first_result.version == "v000002"
    assert first_result.reused is False
    assert first_result.resolved_conflicts[0]["accepted"] == {
        "sha256": _sha256(second.content),
        "source_key": second.source_key,
        "value": "20 mm",
    }

    del by_source[second.source_key]["supersedes"]
    by_source[first.source_key]["supersedes"] = second.source_key
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(
        proposal_improve_cmd.proposal_version.HeadCasConflict,
        match="HEAD is not requested parent v000001; rerun with the current HEAD",
    ):
        proposal_improve_cmd.improve_from_report(
            "demo", since_version="v000001", report=report
        )

    manifest = _json_object(
        tmp_path / "demo" / "versions" / "v000002" / "manifest.json"
    )
    assert manifest["run_key"] == first_run_key
    assert cast(dict[str, object], manifest["assertions"])["굴착 오차"] == {
        "sha256": _sha256(second.content),
        "source_key": second.source_key,
        "value": "20 mm",
    }


def test_parent_value_conflict_requires_explicit_accept_directive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROPOSAL_ROOT", str(tmp_path))
    _parent(
        tmp_path,
        assertions={
            "굴착 오차": {
                "sha256": "",
                "source_key": "parent:kpi",
                "value": "10 mm",
            }
        },
    )
    changed = _kpi_source("note:meetings/kpi-change.md", "20 mm")
    report = _collect(tmp_path, (changed,), marker="d")

    with pytest.raises(proposal_improve_cmd.DeltaConflictUnresolved):
        proposal_improve_cmd.improve_from_report(
            "demo", since_version="v000001", report=report
        )

    digest = _sha256(changed.content)
    result = proposal_improve_cmd.improve_from_report(
        "demo",
        since_version="v000001",
        report=report,
        resolutions={digest: "accept"},
    )

    child = tmp_path / "demo" / "versions" / result.version
    assertions = cast(
        dict[str, dict[str, str]], _json_object(child / "manifest.json")["assertions"]
    )
    assert assertions["굴착 오차"]["value"] == "20 mm"
    assert result.resolved_conflicts


def test_same_delta_report_reuses_version_without_new_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROPOSAL_ROOT", str(tmp_path))
    _parent(tmp_path)
    report = _collect(tmp_path, (_background_source(),), marker="e")

    first = proposal_improve_cmd.improve_from_report(
        "demo", since_version="v000001", report=report
    )
    second = proposal_improve_cmd.improve_from_report(
        "demo", since_version="v000001", report=report
    )

    versions = sorted(path.name for path in (tmp_path / "demo" / "versions").iterdir())
    assert first.version == second.version == "v000002"
    assert first.reused is False
    assert second.reused is True
    assert versions == ["v000001", "v000002"]


def test_changelog_markdown_is_byte_identical_after_two_regenerations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROPOSAL_ROOT", str(tmp_path))
    store, _parent_path = _parent(tmp_path)
    report = _collect(tmp_path, (_background_source(),), marker="f")
    proposal_improve_cmd.improve_from_report(
        "demo", since_version="v000001", report=report
    )

    store.regenerate_changelog("demo")
    first = (tmp_path / "demo" / "CHANGELOG.md").read_bytes()
    store.regenerate_changelog("demo")
    second = (tmp_path / "demo" / "CHANGELOG.md").read_bytes()

    assert first == second


def test_title_or_goal_statement_change_regenerates_every_section_and_figure_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROPOSAL_ROOT", str(tmp_path))
    figures = tuple(
        FigureSpec(
            f"fig-s{section}-01",
            f"s{section}",
            (f"public:claim-{section}",),
            f"public section {section}",
            f"section {section}",
            _sha256(fake_png(f"figure-{section}")),
            section,
        )
        for section in range(5)
    )
    _parent(tmp_path, figures=figures)
    source = proposal_delta.DeltaSource(
        "wiki", "wiki:decision/title", "과제 제목 변경: 자율 굴착 플랫폼\n".encode()
    )
    report = _collect(tmp_path, (source,), marker="1")

    result = proposal_improve_cmd.improve_from_report(
        "demo", since_version="v000001", report=report
    )

    assert result.plan.sections == frozenset(range(5))
    assert result.plan.figures == frozenset(figure.figure_id for figure in figures)
    assert result.plan.figure_prompts is True
    assert result.reused_figures == 0


def test_ambiguous_mixed_content_uses_exact_widest_regeneration_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROPOSAL_ROOT", str(tmp_path))
    figures = tuple(
        FigureSpec(
            f"fig-s{section}-ambiguous",
            f"s{section}",
            (f"public:ambiguous-{section}",),
            f"unclassified evidence {section}",
            f"unclassified figure {section}",
            _sha256(fake_png(f"ambiguous-{section}")),
            section,
        )
        for section in range(5)
    )
    _parent(tmp_path, figures=figures)
    source = proposal_delta.DeltaSource(
        "note",
        "note:mixed-content.bin",
        b"\x00opaque mixed content\xff without recognized policy markers\n",
    )
    report = _collect(tmp_path, (source,), marker="7")

    result = proposal_improve_cmd.improve_from_report(
        "demo", since_version="v000001", report=report
    )

    assert result.plan.sections == frozenset({0, 1, 2, 3, 4})
    assert result.plan.tables == frozenset(
        {"prior-research", "tech-gap", "kpi", "gantt"}
    )
    assert result.plan.figures == frozenset(
        {
            "fig-s0-ambiguous",
            "fig-s1-ambiguous",
            "fig-s2-ambiguous",
            "fig-s3-ambiguous",
            "fig-s4-ambiguous",
        }
    )
    assert result.plan.figure_prompts is True


def test_malformed_index_fails_without_creating_a_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROPOSAL_ROOT", str(tmp_path))
    _parent(tmp_path)
    report = _collect(tmp_path, (_background_source(),), marker="2")
    index_path = report.destination / "delta" / "INDEX.json"
    index = cast(
        list[dict[str, object]], json.loads(index_path.read_text(encoding="utf-8"))
    )
    del index[0]["collected_at"]
    index_path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(proposal_improve_cmd.ImproveInputError, match="INDEX"):
        proposal_improve_cmd.improve_from_report(
            "demo", since_version="v000001", report=report
        )

    versions = sorted(path.name for path in (tmp_path / "demo" / "versions").iterdir())
    assert versions == ["v000001"]


def test_leftover_run_key_staging_is_rebuilt_not_promoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROPOSAL_ROOT", str(tmp_path))
    store, _parent_path = _parent(tmp_path)
    source = _background_source()
    report = _collect(tmp_path, (source,), marker="4")
    run_key = store.compute_run_key(
        store.manifest_sha256("demo", "v000001"),
        [_sha256(source.content)],
        {"resolve": {}},
        "seed-template",
        "30-page",
        {"engine": "test-pin"},
    )
    stale = store.begin("demo", run_key)
    assert isinstance(stale, Staging)
    (stale.path / "out" / "stale.marker").write_text(
        "must not publish", encoding="utf-8"
    )

    result = proposal_improve_cmd.improve_from_report(
        "demo", since_version="v000001", report=report, store=store
    )

    child = tmp_path / "demo" / "versions" / result.version
    assert result.version == "v000002"
    assert not (child / "out" / "stale.marker").exists()
    assert not stale.path.exists()


def test_interruption_before_promote_keeps_versions_intact_and_can_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROPOSAL_ROOT", str(tmp_path))
    store, _parent_path = _parent(tmp_path)
    report = _collect(tmp_path, (_background_source(),), marker="5")
    real_promote = store.promote

    def interrupt(slug: str, staging: Staging, manifest: dict[str, object]) -> str:
        del slug, staging, manifest
        raise KeyboardInterrupt

    monkeypatch.setattr(store, "promote", interrupt)
    with pytest.raises(KeyboardInterrupt):
        proposal_improve_cmd.improve_from_report(
            "demo", since_version="v000001", report=report, store=store
        )

    versions = sorted(path.name for path in (tmp_path / "demo" / "versions").iterdir())
    assert versions == ["v000001"]
    monkeypatch.setattr(store, "promote", real_promote)
    resumed = proposal_improve_cmd.improve_from_report(
        "demo", since_version="v000001", report=report, store=store
    )
    assert resumed.version == "v000002"


def test_version_status_lists_head_and_all_versions(tmp_path: Path) -> None:
    store, _parent_path = _parent(tmp_path)

    payload = proposal_improve_cmd.version_status(store, "demo")

    assert payload == {
        "head": "v000001",
        "slug": "demo",
        "state": "versioned",
        "version": "v000001",
        "versions": ["v000001"],
    }

    (tmp_path / "demo" / "versions" / "v000001" / "publish-receipt.json").write_text(
        "{}\n", encoding="utf-8"
    )
    assert proposal_improve_cmd.version_status(store, "demo")["state"] == "published"


def test_cli_consumes_delta_inbox_and_reports_idempotent_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PROPOSAL_ROOT", str(tmp_path))
    _parent(tmp_path)
    _collect(tmp_path, (_background_source(),), marker="3")

    first_rc = proposal_cli.main(
        ["improve", "--slug", "demo", "--since", "v000001", "--json"]
    )
    first = cast(dict[str, object], json.loads(capsys.readouterr().out))
    second_rc = proposal_cli.main(
        ["improve", "--slug", "demo", "--since", "v000001", "--json"]
    )
    second = cast(dict[str, object], json.loads(capsys.readouterr().out))
    version_rc = proposal_cli.main(["version", "--slug", "demo", "--json"])
    status = cast(dict[str, object], json.loads(capsys.readouterr().out))

    assert first_rc == second_rc == version_rc == 0
    assert cast(dict[str, object], first["regeneration"])["sections"] == [1]
    assert first["version"] == second["version"] == "v000002"
    assert first["reused"] is False
    assert second["reused"] is True
    assert status["head"] == "v000002"
    assert status["versions"] == ["v000001", "v000002"]
