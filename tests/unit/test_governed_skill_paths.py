from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_LIVE_ROOT = "/srv/autophagy-skills/live"


def _source(relative_path: str) -> str:
    return (_REPO / relative_path).read_text(encoding="utf-8")


def _non_docstring_literals(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path), feature_version=(3, 11))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return tuple(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    )


def test_calendar_watch_default_when_env_absent_then_uses_live_scripts() -> None:
    # 경로 사본 대신 공유 정의(automation/skill_mount.py)를 경유한다 — 판정은
    # tests/unit/test_skill_mount_definition.py 가 주입된 live 루트로 고정한다.
    source = _source("skills/calendar/scripts/confirm_reaction_watch.py")
    assert 'skill_scripts("calendar", env_var="CALENDAR_SCRIPTS")' in source
    assert f'LIVE_ROOT: Final = Path("{_LIVE_ROOT}")' in _source("automation/skill_mount.py")


def test_coordination_watch_default_when_env_absent_then_uses_live_scripts() -> None:
    source = _source("skills/coordination/scripts/confirm_reaction_watch.py")
    assert 'skill_scripts("coordination", env_var="COORDINATION_SCRIPTS")' in source
    assert f'LIVE_ROOT: Final = Path("{_LIVE_ROOT}")' in _source("automation/skill_mount.py")


def test_coordination_calendar_default_when_env_absent_then_uses_live_cli() -> None:
    source = _source("skills/coordination/scripts/coordinate_io.py")
    assert f'_LIVE_CALENDAR_SCRIPTS: Final = "{_LIVE_ROOT}/calendar/scripts"' in source


def test_mail_digest_default_when_started_then_uses_live_cli() -> None:
    source = _source("skills/mail/scripts/mail_digest_watch.py")
    assert f'CLI = Path("{_LIVE_ROOT}/mail/scripts/triage_cli.py")' in source


def test_mail_triage_default_when_started_then_uses_live_cli() -> None:
    source = _source("skills/mail/scripts/mail_triage_watch.py")
    assert f'CLI = Path("{_LIVE_ROOT}/mail/scripts/triage_cli.py")' in source


def test_mail_calendar_default_when_env_absent_then_uses_live_cli() -> None:
    source = _source("skills/mail/scripts/triage_transport.py")
    assert f'"TRIAGE_CALENDAR_CLI", "{_LIVE_ROOT}/calendar/scripts/calendar_cli.py"' in source


def test_meeting_plugin_default_when_env_absent_then_uses_live_cli() -> None:
    source = _source("skills/meeting/plugin/__init__.py")
    assert f'_LIVE_CLI: Final = "{_LIVE_ROOT}/meeting/scripts/meeting_cli.py"' in source


def test_meeting_rules_defaults_when_env_absent_then_both_use_live_config() -> None:
    source = _source("skills/meeting/scripts/meeting_cli.py")
    assert source.count(f'"{_LIVE_ROOT}/meeting/configs/sensitivity-rules.yaml"') == 2


def test_meeting_prompt_default_when_env_absent_then_uses_live_prompt() -> None:
    source = _source("skills/meeting/scripts/meeting_cli.py")
    assert f'"{_LIVE_ROOT}/meeting/prompts/meeting-extraction-v5.md"' in source


def test_patent_watch_default_when_env_absent_then_uses_live_scripts() -> None:
    source = _source("skills/patent-prep/scripts/patent_export_confirm_reaction_watch.py")
    assert f'_LIVE_SCRIPTS: Final = "{_LIVE_ROOT}/patent-prep/scripts"' in source


def test_wiki_migration_default_when_importing_schema_then_uses_live_scripts() -> None:
    source = _source("automation/migrate-cha-wiki.sh")
    assert f'skill = Path("{_LIVE_ROOT}/wiki/scripts")' in source


def test_calendar_remote_e2e_default_when_run_on_node_then_uses_live_cli() -> None:
    source = _source("tests/e2e/drivers/w3_calendar_remote.py")
    assert f'CLI = "{_LIVE_ROOT}/calendar/scripts/calendar_cli.py"' in source


def test_coordination_remote_e2e_defaults_when_run_on_node_then_use_live_clis() -> None:
    source = _source("tests/e2e/w3_3_runner.sh")
    assert (
        f'CLI="{_LIVE_ROOT}/coordination/scripts/coordinate_cli.py"' in source
        and f'CAL="{_LIVE_ROOT}/calendar/scripts/calendar_cli.py"' in source
    )


@pytest.mark.parametrize(
    ("relative_path", "env_name", "expression", "python_path"),
    (
        ("skills/calendar/scripts/confirm_reaction_watch.py", "CALENDAR_SCRIPTS", "_SCRIPTS", "skills/calendar/scripts"),
        ("skills/coordination/scripts/confirm_reaction_watch.py", "COORDINATION_SCRIPTS", "_SCRIPTS", "skills/coordination/scripts"),
        ("skills/coordination/scripts/coordinate_io.py", "CALENDAR_SCRIPTS", "calendar_scripts()", "skills/coordination/scripts"),
        ("skills/mail/scripts/triage_transport.py", "TRIAGE_CALENDAR_CLI", "_env_path('TRIAGE_CALENDAR_CLI', 'wrong')", "skills/mail/scripts"),
        ("skills/meeting/plugin/__init__.py", "MEETING_CLI", "_CLI_PATH", "skills/meeting/plugin"),
        ("skills/meeting/scripts/meeting_cli.py", "MEETING_RULES_FILE", "_env_path('MEETING_RULES_FILE', 'wrong')", "skills/meeting/scripts"),
        ("skills/meeting/scripts/meeting_cli.py", "MEETING_PROMPT_FILE", "_env_path('MEETING_PROMPT_FILE', 'wrong')", "skills/meeting/scripts"),
        ("skills/patent-prep/scripts/patent_export_confirm_reaction_watch.py", "PATENT_SCRIPTS", "_SCRIPTS", "skills/patent-prep"),
    ),
)
def test_governed_path_override_when_env_set_then_override_wins(
    tmp_path: Path,
    relative_path: str,
    env_name: str,
    expression: str,
    python_path: str,
) -> None:
    override = tmp_path / "override"
    override.mkdir()
    if env_name == "CALENDAR_SCRIPTS":
        (override / "calendar_cli.py").touch()
    script = (
        "import runpy\n"
        f"namespace = runpy.run_path({str(_REPO / relative_path)!r})\n"
        f"print(eval({expression!r}, namespace))\n"
    )
    environment = dict(os.environ)
    environment[env_name] = str(override)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(_REPO), str(_REPO / python_path), environment.get("PYTHONPATH", ""))
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        cwd=_REPO,
    )
    assert result.returncode == 0 and result.stdout.strip() == str(override), result.stderr


def test_governed_sources_when_scanned_then_have_no_home_root_defaults() -> None:
    python_sources = sorted((_REPO / "skills").glob("**/*.py"))
    python_sources += sorted((_REPO / "automation").glob("**/*.py"))
    offenders = [
        str(path.relative_to(_REPO))
        for path in python_sources
        if any("~/.hermes/skills/" in literal for literal in _non_docstring_literals(path))
    ]
    shell_sources = sorted((_REPO / "skills").glob("**/*.sh"))
    shell_sources += sorted((_REPO / "automation").glob("**/*.sh"))
    offenders += [
        str(path.relative_to(_REPO))
        for path in shell_sources
        if any(
            "~/.hermes/skills/" in line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
    ]
    assert not offenders, "writable governed-skill defaults remain:\n" + "\n".join(offenders)
