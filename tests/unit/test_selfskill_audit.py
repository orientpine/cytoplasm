from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

_NOW = datetime(2026, 8, 15, 0, 0, tzinfo=UTC)
_REPO = Path(__file__).resolve().parents[2]
_WATCH_PATH = _REPO / "automation" / "selfskill_audit" / "cron" / "selfskill_audit_watch.py"
sys.path.insert(0, str(_REPO))

from automation.selfskill_audit import ledger, report  # noqa: E402


def _skill(home: Path, name: str, body: str) -> Path:
    skill = home / ".hermes" / "skills" / name
    skill.mkdir(parents=True)
    _ = (skill / "SKILL.md").write_text(body, encoding="utf-8")
    return skill


def _usage(home: Path, payload: dict[str, dict[str, str | bool | None]]) -> None:
    path = home / ".hermes" / "skills" / ".usage.json"
    _ = path.write_text(json.dumps(payload), encoding="utf-8")


def _load_watch() -> ModuleType:
    spec = importlib.util.spec_from_file_location("selfskill_audit_watch_test", _WATCH_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_ledger_when_a_skill_is_created_then_records_a_content_hashed_delta(tmp_path: Path) -> None:
    # Given
    home = tmp_path / "agent"
    skill = _skill(home, "agent-notes", "---\nname: agent-notes\n---\nbody-v1\n")
    _usage(home, {"agent-notes": {"created_by": "agent", "agent_created": True, "pinned": False, "archived_at": None}})

    # When
    result = ledger.audit(home, now=_NOW)

    # Then
    assert [(delta.action.value, delta.name) for delta in result.deltas] == [("created", "agent-notes")]
    assert result.deltas[0].sha256 == ledger.skill_digest(skill)
    assert result.deltas[0].provenance == "agent"
    stored = json.loads(result.ledger_path.read_text(encoding="utf-8"))
    assert stored["sha256"] == result.deltas[0].sha256
    assert stored["timestamp"] == "2026-08-15T00:00:00Z"

def test_ledger_when_a_skill_sits_under_a_category_then_it_is_still_detected(tmp_path: Path) -> None:
    # Given: Hermes files agent-authored skills under a category directory, which is where
    # the peer account's live self-authored skills actually sit (software-development/<name>).
    home = tmp_path / "agent"
    skill = home / ".hermes" / "skills" / "software-development" / "agent-notes"
    skill.mkdir(parents=True)
    _ = (skill / "SKILL.md").write_text("---\nname: agent-notes\n---\nbody-v1\n", encoding="utf-8")
    _usage(home, {"agent-notes": {"created_by": "agent", "agent_created": True, "pinned": False, "archived_at": None}})

    # When
    result = ledger.audit(home, now=_NOW)

    # Then
    assert [(delta.action.value, delta.name) for delta in result.deltas] == [("created", "agent-notes")]
    assert result.deltas[0].sha256 == ledger.skill_digest(skill)
    assert result.deltas[0].provenance == "agent"


def test_ledger_when_a_category_holds_no_skill_then_it_is_not_reported_as_one(tmp_path: Path) -> None:
    # Given: an empty category directory and Hermes' own state directories must not be
    # mistaken for skills just because the scan now descends one level.
    home = tmp_path / "agent"
    root = home / ".hermes" / "skills"
    (root / "empty-category").mkdir(parents=True)
    (root / ".hub").mkdir()
    _ = (root / ".hub" / "taps.json").write_text("{}\n", encoding="utf-8")

    # When
    result = ledger.audit(home, now=_NOW)

    # Then
    assert result.deltas == ()


def test_ledger_when_nothing_changed_then_produces_no_delta_and_no_dm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    home = tmp_path / "agent"
    _skill(home, "agent-notes", "---\nname: agent-notes\n---\nstable\n")
    _usage(home, {"agent-notes": {"created_by": "agent", "agent_created": True, "pinned": False, "archived_at": None}})
    sent: list[str] = []
    monkeypatch.setattr(report, "notify_owner", lambda body: sent.append(body) is None or True)
    assert report.run_once(home=home, account_label="agent", now=_NOW) == 0
    sent.clear()

    # When
    result = ledger.audit(home, now=_NOW)
    exit_code = report.run_once(home=home, account_label="agent", now=_NOW)

    # Then
    assert result.deltas == ()
    assert exit_code == 0
    assert sent == []


def test_ledger_when_a_skill_is_archived_then_records_the_archive_delta(tmp_path: Path) -> None:
    # Given
    home = tmp_path / "agent"
    active = _skill(home, "agent-notes", "---\nname: agent-notes\n---\nbody-v1\n")
    _usage(home, {"agent-notes": {"created_by": "agent", "agent_created": True, "pinned": False, "archived_at": None}})
    _ = ledger.audit(home, now=_NOW)
    archive = home / ".hermes" / "skills" / ".archive" / "agent-notes"
    archive.parent.mkdir()
    active.rename(archive)
    _usage(home, {"agent-notes": {"created_by": "agent", "agent_created": True, "pinned": False, "archived_at": "2026-08-15T01:00:00Z"}})

    # When
    result = ledger.audit(home, now=datetime(2026, 8, 15, 1, 0, tzinfo=UTC))

    # Then
    assert [(delta.action.value, delta.name) for delta in result.deltas] == [("archived", "agent-notes")]
    assert result.deltas[0].sha256 == ledger.skill_digest(archive)


def test_report_when_rendered_then_contains_no_file_bodies_and_no_surface_literals(tmp_path: Path) -> None:
    # Given
    home = tmp_path / "agent"
    _skill(home, "agent-notes", "PRIVATE-BODY-MARKER\n#approvals\nowner-dm\n")
    _usage(home, {"agent-notes": {"created_by": "agent", "agent_created": True, "pinned": False, "archived_at": None}})
    result = ledger.audit(home, now=_NOW)

    # When
    rendered = report.render_summary(result.deltas, account_label="agent")

    # Then
    assert "agent-notes" in rendered
    assert result.deltas[0].sha256[:12] in rendered
    assert "PRIVATE-BODY-MARKER" not in rendered
    assert "#approvals" not in rendered
    assert "owner-dm" not in rendered.lower()
    assert "✅" not in rendered
    assert "⛔" not in rendered


def test_state_files_when_written_then_live_outside_the_checkout_with_0600(tmp_path: Path) -> None:
    # Given
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    home = tmp_path / "agent-home"
    _skill(home, "agent-notes", "content")
    _usage(home, {"agent-notes": {"created_by": "agent", "agent_created": True, "pinned": False, "archived_at": None}})

    # When
    result = ledger.audit(home, now=_NOW)

    # Then
    assert result.state_path == home / ".hermes" / "selfskill-audit" / "state.json"
    assert result.ledger_path == home / ".hermes" / "selfskill-audit" / "ledger.jsonl"
    assert checkout not in result.state_path.parents
    assert stat.S_IMODE(result.state_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(result.ledger_path.stat().st_mode) == 0o600


def test_watch_when_spawning_children_then_passes_credentials_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    watch = _load_watch()
    home = tmp_path / "agent"
    home.mkdir()
    secrets = home / ".env.secrets"
    _ = secrets.write_text("DISCORD_BOT_TOKEN=fixture-token\nAUTOPHAGY_OWNER_ID=fixture-owner\n", encoding="utf-8")
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("AUTOPHAGY_OWNER_ID", raising=False)
    captured: list[dict[str, str]] = []

    def run_child(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        captured.append(environment)
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(watch.subprocess, "run", run_child)
    watch._load_env_secrets(secrets)

    # When
    exit_code = watch.run_once(tmp_path / "repo")

    # Then
    assert exit_code == 0
    assert captured[0]["DISCORD_BOT_TOKEN"] == "fixture-token"
    assert captured[0]["AUTOPHAGY_OWNER_ID"] == "fixture-owner"
    assert captured[0]["AUTOPHAGY_REPO_ROOT"] == str(tmp_path / "repo")


def test_watch_when_resolving_repo_root_then_prefers_release_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    watch = _load_watch()
    current = tmp_path / "release-current"
    mirror = tmp_path / "mirror"
    (current / "automation").mkdir(parents=True)
    (mirror / "automation").mkdir(parents=True)
    monkeypatch.delenv("AUTOPHAGY_REPO_ROOT", raising=False)
    monkeypatch.setattr(watch, "RELEASE_CURRENT", current)
    monkeypatch.setattr(watch, "RESIDENT_MIRROR", mirror)

    # When
    resolved = watch._runtime_root()

    # Then
    assert resolved == current
