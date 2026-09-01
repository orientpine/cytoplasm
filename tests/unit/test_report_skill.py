from __future__ import annotations

import os
import sys
from datetime import date
from importlib import import_module
from types import SimpleNamespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "report" / "scripts"
sys.path.insert(0, str(SCRIPTS.parents[1]))

report_cli = import_module("report.scripts.report_cli")
report_core = import_module("report.scripts.report_core")
report_llm = import_module("report.scripts.report_llm")
report_sensitivity = import_module("report.scripts.report_sensitivity")


def _write_note(path: Path, title: str, body: str, timestamp: int) -> None:
    path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    os.utime(path, (timestamp, timestamp))


def test_select_notes_orders_recent_markdown_and_skips_hidden(tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    _write_note(notes / "old.md", "Old", "old body", 10)
    _write_note(notes / "recent.md", "Recent", "recent body", 30)
    hidden = notes / ".private"
    hidden.mkdir()
    _write_note(hidden / "ignored.md", "Ignored", "hidden body", 40)

    selected = report_core.select_notes(notes, limit=2)

    assert [note.title for note in selected] == ["Recent", "Old"]


def test_assemble_report_has_required_sections(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    _write_note(note, "Cell clearance", "evidence", 10)
    selected = report_core.select_notes(tmp_path, limit=1)

    report = report_core.assemble_report("Weekly synthesis", selected, "Draft analysis.")

    assert report.startswith("# Weekly synthesis\n")
    assert "## 자료 범위" in report
    assert "## 핵심 내용" in report
    assert "## 근거 노트" in report


def test_render_slides_has_reveal_structure_and_title_count() -> None:
    report = "# Weekly synthesis\n\n## 자료 범위\n\nScope.\n\n## 핵심 내용\n\nAnalysis.\n"

    deck = report_core.render_slides(report)

    assert '<div class="reveal">' in deck.html
    assert "Reveal.initialize" in deck.html
    assert deck.html.count("<section") == 3
    assert deck.titles == ("Weekly synthesis", "자료 범위", "핵심 내용")


def test_generate_script_covers_each_slide_title() -> None:
    script = report_core.generate_script(("Weekly synthesis", "자료 범위", "핵심 내용"))

    assert script.startswith("# 발표 대본\n")
    assert "## 슬라이드 1 — Weekly synthesis" in script
    assert "## 슬라이드 3 — 핵심 내용" in script


def test_organize_notes_writes_a_private_weekly_index(tmp_path: Path) -> None:
    _write_note(tmp_path / "first.md", "First", "one", 10)
    _write_note(tmp_path / "second.md", "Second", "two", 20)

    index = report_core.organize_notes(tmp_path, "2026-W29")

    assert index == tmp_path / "_weekly" / "notes-2026-W29.md"
    assert index.exists()
    assert "# 주간 노트 정리" in index.read_text(encoding="utf-8")
    assert (index.stat().st_mode & 0o777) == 0o600


def test_sensitive_notes_route_only_to_openai_codex(tmp_path: Path) -> None:
    rules = report_sensitivity.load_rules(ROOT / "configs" / "sensitivity-rules.yaml")
    note = tmp_path / "sensitive.md"
    _write_note(note, "Private", "patent filing planning", 10)
    selected = report_core.select_notes(tmp_path, limit=1)

    route = report_sensitivity.route_notes(selected, rules)

    assert route.provider == "openai-codex"
    assert route.model == "gpt-5.4"
    assert route.sensitive is True


def test_report_publish_calls_use_weekly_bundle_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report_path = tmp_path / "source.md"
    report_path.write_text("# Weekly\n\n## 자료 범위\n\nScope.\n\n## 핵심 내용\n\nAnalysis.\n", encoding="utf-8")
    response = tmp_path / "response.md"
    response.write_text("Draft", encoding="utf-8")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "note.md").write_text("# Note\n\nBody", encoding="utf-8")
    calls: list[tuple[str, str, str, date | None]] = []

    def publish(kind: str, title: str, artifacts: list[tuple[Path, str]], *, on: date | None = None, **_: object) -> object:
        calls.append((kind, title, artifacts[0][1], on))
        return SimpleNamespace(links=("https://drive.test/file",))

    import automation.drive_outputs as drive_outputs
    monkeypatch.setattr(drive_outputs, "publish_best_effort", publish)
    monkeypatch.setenv("REPORT_RULES_PATH", str(ROOT / "configs" / "sensitivity-rules.yaml"))
    period = date(2026, 8, 10)
    assert report_cli._report(SimpleNamespace(
        notes_root=str(tmp_path / "notes"), outputs_root=str(tmp_path / "outputs"), query="",
        limit=12, title="", response_file=str(response), with_evidence=False, period_date=period,
    )) == 0
    assert report_cli._slides(SimpleNamespace(report=str(report_path), outputs_root=str(tmp_path / "outputs"), period_date=period)) == 0
    assert report_cli._script(SimpleNamespace(report=str(report_path), outputs_root=str(tmp_path / "outputs"), slides="", period_date=period)) == 0
    assert calls == [
        ("report", "주간연구동향", "주간연구동향", period),
        ("report", "주간연구동향", "발표슬라이드", period),
        ("report", "주간연구동향", "발표스크립트", period),
    ]


def test_report_real_facade_disabled_makes_zero_runner_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from automation.drive_client import DriveClient
    import automation.drive_outputs as drive_outputs

    monkeypatch.delenv("DRIVE_PUBLISH_ENABLED", raising=False)
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> dict[str, object]:
        calls.append(argv)
        return {}

    client = DriveClient("fake-gws", tmp_path / "folders.json", runner=runner)
    monkeypatch.setattr(drive_outputs, "client_from_environment", lambda: client)
    response = tmp_path / "response.md"
    response.write_text("Draft", encoding="utf-8")
    monkeypatch.setattr(report_cli.report_llm, "generate", lambda *_: "Draft")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "note.md").write_text("# Note\n\nBody", encoding="utf-8")
    assert report_cli._report(SimpleNamespace(
        notes_root=str(tmp_path / "notes"), outputs_root=str(tmp_path / "outputs"), query="",
        limit=12, title="", response_file=str(response), with_evidence=False, period_date=None,
    )) == 0
    assert calls == []


def test_report_import_failure_keeps_local_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    response = tmp_path / "response.md"
    response.write_text("Draft", encoding="utf-8")
    original = report_cli.import_module
    def fail_facade(name: str, package: str | None = None) -> object:
        if name == "automation.drive_outputs":
            raise ImportError("injected")
        return original(name, package)
    monkeypatch.setattr(report_cli, "import_module", fail_facade)
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "note.md").write_text("# Note\n\nBody", encoding="utf-8")
    assert report_cli._report(SimpleNamespace(
        notes_root=str(tmp_path / "notes"), outputs_root=str(tmp_path / "outputs"), query="",
        limit=12, title="", response_file=str(response), with_evidence=False, period_date=None,
    )) == 0
    assert next((tmp_path / "outputs").glob("report-*.md")).exists()
    assert "DRIVE-PUBLISH-SKIP reason=ImportError" in capsys.readouterr().err


def test_report_with_no_notes_returns_insufficient_material(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = report_cli.main(
        ["report", "--notes-root", str(tmp_path / "notes"), "--outputs-root", str(tmp_path / "outputs")]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "자료 부족" in captured.out


def test_glm_hermes_child_receives_key_from_secrets_when_parent_environment_lacks_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    binary = tmp_path / ".local" / "bin" / "hermes"
    binary.parent.mkdir(parents=True)
    _ = binary.write_text(
        '#!/bin/sh\n[ -n "$LITELLM_AGENT_KEY" ] || exit 9\nprintf "draft"\n',
        encoding="utf-8",
    )
    _ = binary.chmod(0o755)
    _ = (tmp_path / ".env.secrets").write_text(
        "LITELLM_AGENT_KEY=report-fallback-key\n", encoding="utf-8"
    )
    monkeypatch.delenv("LITELLM_AGENT_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    route = report_sensitivity.Route(
        provider="custom:litellm", model="glm-main", sensitive=False, tags=()
    )

    # When
    result = report_llm.generate("prompt", route)

    # Then
    assert result == "draft"


def test_cli_runs_as_main_from_hash_named_deploy_layout(tmp_path: Path) -> None:
    # Regression: deployed skills live at
    # /srv/autophagy-skills/releases/report/<sha256>/scripts/report_cli.py, so the
    # package dir is hash-named (NOT "report"). Running the CLI as __main__ there
    # must resolve its siblings via flat imports, not `report.scripts.*`.
    # Before the dual-mode import fix this raised ModuleNotFoundError: No module
    # named 'report' (exit 1), breaking the notes-weekly-organize cron.
    import subprocess

    deploy_scripts = tmp_path / "releases" / "report" / "deadbeefhash" / "scripts"
    deploy_scripts.mkdir(parents=True)
    for module in SCRIPTS.glob("report_*.py"):
        _ = (deploy_scripts / module.name).write_text(
            module.read_text(encoding="utf-8"), encoding="utf-8"
        )
    notes = tmp_path / "notes"
    notes.mkdir()
    _write_note(notes / "one.md", "One", "body one", 10)

    result = subprocess.run(
        [sys.executable, str(deploy_scripts / "report_cli.py"),
         "organize", "--notes-root", str(notes)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "NOTES-ORGANIZED" in result.stdout
    assert (notes / "_weekly").is_dir()
