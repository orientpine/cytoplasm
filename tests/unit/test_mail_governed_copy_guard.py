"""A copy of the mail skill outside the governed mount must not create drafts.

RED-first contract for the 2026-09-01 incident: the reply to a colleague went out
WITHOUT the quoted original although the quote feature was mounted at 04:22Z —
the agent ran ``/srv/autophagy-agents/skills/mail/scripts/triage_cli.py`` (the
ops mirror, 121 commits behind and frozen dirty since 08-29) instead of
``/srv/autophagy-skills/live/mail/scripts/triage_cli.py``. "배포됨 ≠ 실행됨":
on a host that carries a governed mount, only that mount may run mutating mail
commands. The judgment lives in ``mail_runtime.governed_copy_refusal`` and the
live-root injection is the single ``AUTOPHAGY_SKILL_LIVE_ROOT`` name that
``automation.skill_mount`` already owns (sandbox/e2e declare their own root).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import mail_runtime  # noqa: E402
import triage_cli  # noqa: E402
from automation import skill_mount  # noqa: E402

GOVERNED_ROOT = Path("/srv/autophagy-skills/live")


def _fake_store(tmp_path: Path) -> tuple[Path, Path]:
    """(live_root, release_scripts) shaped like /srv/autophagy-skills/{live,releases}."""
    release = tmp_path / "releases" / "mail" / ("a" * 8)
    (release / "scripts").mkdir(parents=True)
    (release / "scripts" / "triage_cli.py").write_text("# governed copy\n", encoding="utf-8")
    live = tmp_path / "live"
    live.mkdir()
    (live / "mail").symlink_to(release, target_is_directory=True)
    return live, release / "scripts"


def _stale_copy(tmp_path: Path) -> Path:
    mirror = tmp_path / "autophagy-agents" / "skills" / "mail" / "scripts"
    mirror.mkdir(parents=True)
    script = mirror / "triage_cli.py"
    script.write_text("# stale mirror copy\n", encoding="utf-8")
    return script


def test_live_root_env_name_is_the_one_skill_mount_owns() -> None:
    assert mail_runtime.LIVE_ROOT_ENV == skill_mount.LIVE_ROOT_ENV
    assert mail_runtime.GOVERNED_LIVE_ROOT == skill_mount.LIVE_ROOT == GOVERNED_ROOT


def test_stale_copy_is_refused_with_the_governed_path(tmp_path: Path) -> None:
    live, governed_scripts = _fake_store(tmp_path)
    stale = _stale_copy(tmp_path)
    refusal = mail_runtime.governed_copy_refusal(stale, env={mail_runtime.LIVE_ROOT_ENV: str(live)})
    assert refusal is not None
    assert refusal.startswith("STALE-SKILL-COPY-BLOCK")
    assert str(live / "mail" / "scripts" / "triage_cli.py") in refusal
    assert str(stale) in refusal


def test_governed_copy_runs_through_the_live_symlink_or_the_release_path(tmp_path: Path) -> None:
    live, governed_scripts = _fake_store(tmp_path)
    env = {mail_runtime.LIVE_ROOT_ENV: str(live)}
    assert mail_runtime.governed_copy_refusal(live / "mail" / "scripts" / "triage_cli.py", env=env) is None
    assert mail_runtime.governed_copy_refusal(governed_scripts / "triage_cli.py", env=env) is None


def test_host_without_a_governed_mount_is_not_guarded(tmp_path: Path) -> None:
    stale = _stale_copy(tmp_path)
    env = {mail_runtime.LIVE_ROOT_ENV: str(tmp_path / "no-such-live")}
    assert mail_runtime.governed_copy_refusal(stale, env=env) is None
    # Given: a live root that mounts other skills but not mail.
    other = tmp_path / "live-other"
    (other / "calendar" / "scripts").mkdir(parents=True)
    assert mail_runtime.governed_copy_refusal(stale, env={mail_runtime.LIVE_ROOT_ENV: str(other)}) is None


def test_repo_shaped_root_declares_the_checkout_copy_governed(tmp_path: Path) -> None:
    # sandbox scenario.sh / e2e actors point the env at <repo>/skills so their own copy is the mount
    script = _REPO / "skills" / "mail" / "scripts" / "triage_cli.py"
    env = {mail_runtime.LIVE_ROOT_ENV: str(_REPO / "skills")}
    assert mail_runtime.governed_copy_refusal(script, env=env) is None


def _cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["triage_cli", *argv])
    return triage_cli.main()


def test_cli_mutating_command_from_stale_copy_exits_3_before_touching_mail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    live, _governed = _fake_store(tmp_path)
    stale = _stale_copy(tmp_path)
    monkeypatch.setenv(mail_runtime.LIVE_ROOT_ENV, str(live))
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setattr(triage_cli, "__file__", str(stale))

    def never(_uid: str) -> dict:
        raise AssertionError("a stale copy must be refused before any mail read")

    monkeypatch.setattr(triage_cli, "_get_mail", never)
    monkeypatch.setattr(triage_cli.triage_mode, "effective_mode", lambda: "full-go")
    for argv in (
        ("draft", "--uid", "u-1", "--instruction", "x", "--no-post"),
        ("compose", "--to", "a@b.cd", "--subject", "s", "--body", "b", "--no-post"),
    ):
        assert _cli(monkeypatch, *argv) == 3
        err = capsys.readouterr().err
        assert "GATE-REFUSED STALE-SKILL-COPY-BLOCK" in err
        assert str(live / "mail" / "scripts" / "triage_cli.py") in err
    assert not list((tmp_path / "gate").rglob("*.json"))


def test_cli_read_only_mode_is_not_guarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    live, _governed = _fake_store(tmp_path)
    monkeypatch.setenv(mail_runtime.LIVE_ROOT_ENV, str(live))
    monkeypatch.setenv("TRIAGE_MAIL_MODE_FILE", str(tmp_path / "absent-mode.json"))
    monkeypatch.setenv("TRIAGE_MAIL_MODE_REPO", str(tmp_path / "absent-repo-mode.json"))
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "triage.db"))
    monkeypatch.setattr(triage_cli, "__file__", str(_stale_copy(tmp_path)))
    assert _cli(monkeypatch, "mode") == 0
    assert "MODE effective=" in capsys.readouterr().out
