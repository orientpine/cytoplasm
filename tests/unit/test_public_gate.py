"""Does the public leak gate refuse what would publish private data, without echoing it?

Once development moves to the public repository, a pushed branch is published the moment
it is pushed. These tests pin what `automation/public_gate.py` must refuse at commit, push
and PR time, and that its report never prints the value it refused (public CI logs).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from automation import public_gate

_SECRET_NAME = "Zyxwvut Privatename"
_TAILNET = ".".join(("100", "101", "7", "9"))


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args), capture_output=True, text=True, check=True
    ).stdout


def _commit(repo: Path, message: str = "change") -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@test.invalid", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "public"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text(f"maintainer: {_SECRET_NAME}\n", encoding="utf-8")
    _commit(root, "initial")
    return root


@pytest.fixture
def denylist(tmp_path: Path) -> Path:
    path = tmp_path / "denylist.txt"
    path.write_text(f"# private\n{_SECRET_NAME}\n", encoding="utf-8")
    return path


def _run(repo: Path, denylist: Path | None, *argv: str) -> int:
    prefix = ["--repo", str(repo)] + (["--denylist", str(denylist)] if denylist else [])
    return public_gate.main([*prefix, *argv])


def test_forced_session_evidence_is_refused(repo: Path, denylist: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / ".omo" / "evidence").mkdir(parents=True)
    (repo / ".omo" / "evidence" / "run.txt").write_text("evidence\n", encoding="utf-8")
    _git(repo, "add", "-f", ".omo/evidence/run.txt")

    assert _run(repo, denylist, "staged") == public_gate.EXIT_FINDINGS
    assert ".omo/evidence/run.txt: forbidden path" in capsys.readouterr().err


def test_added_private_literal_is_refused_without_echoing_it(
    repo: Path, denylist: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "notes.md").write_text(f"ask {_SECRET_NAME.lower()} first\n", encoding="utf-8")
    _git(repo, "add", "notes.md")

    assert _run(repo, denylist, "staged") == public_gate.EXIT_FINDINGS
    err = capsys.readouterr().err
    assert "notes.md:1: denylist entry #1" in err
    assert _SECRET_NAME.lower() not in err.lower()


def test_a_value_already_present_does_not_block_an_unrelated_edit(repo: Path, denylist: Path) -> None:
    (repo / "README.md").write_text(f"maintainer: {_SECRET_NAME}\nnew line\n", encoding="utf-8")
    _git(repo, "add", "README.md")

    assert _run(repo, denylist, "staged") == 0


def test_installation_topology_is_refused_without_echoing_it(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "hosts.md").write_text(f"node at {_TAILNET}\n", encoding="utf-8")
    _git(repo, "add", "hosts.md")

    assert _run(repo, None, "staged") == public_gate.EXIT_FINDINGS
    err = capsys.readouterr().err
    assert "hosts.md:1: topology (tailnet address)" in err
    assert _TAILNET not in err


def test_commit_message_carrying_a_private_literal_is_refused(repo: Path, denylist: Path, tmp_path: Path) -> None:
    message = tmp_path / "MSG"
    message.write_text(f"fix: as {_SECRET_NAME} asked\n# comment lines are ignored\n", encoding="utf-8")

    assert _run(repo, denylist, "message", str(message)) == public_gate.EXIT_FINDINGS


def test_configured_but_unreadable_denylist_fails_closed(repo: Path, tmp_path: Path) -> None:
    assert _run(repo, tmp_path / "absent.txt", "staged") == public_gate.EXIT_CONFIG


def test_range_checks_commit_messages_and_pr_text(
    repo: Path, denylist: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "code.py").write_text("VALUE = 1\n", encoding="utf-8")
    head = _commit(repo, "feat: clean change")
    assert _run(repo, denylist, "range", base, head) == 0

    monkeypatch.setenv("PR_TEXT", f"reviewed with {_SECRET_NAME}")
    assert _run(repo, denylist, "range", base, head, "--text-env", "PR_TEXT") == public_gate.EXIT_FINDINGS

    (repo / "code.py").write_text("VALUE = 2\n", encoding="utf-8")
    leaky = _commit(repo, f"fix: thanks {_SECRET_NAME}")
    assert _run(repo, denylist, "range", base, leaky) == public_gate.EXIT_FINDINGS


def test_private_source_skips_excluded_paths_and_messages(repo: Path, denylist: Path, tmp_path: Path) -> None:
    (repo / ".omo").mkdir()
    (repo / ".omo" / "plan.md").write_text("plan\n", encoding="utf-8")
    (repo / "configs").mkdir()
    (repo / "configs" / "public-export-manifest.txt").write_text(".omo/\ndocs/ops/\n", encoding="utf-8")
    base = _commit(repo, "private source")

    (repo / "docs" / "ops").mkdir(parents=True)
    (repo / "docs" / "ops" / "runbook.md").write_text(f"call {_SECRET_NAME}\n", encoding="utf-8")
    excluded = _commit(repo, f"docs: runbook from {_SECRET_NAME}")
    assert _run(repo, denylist, "range", base, excluded) == 0

    (repo / "code.py").write_text(f"OWNER = '{_SECRET_NAME}'\n", encoding="utf-8")
    published = _commit(repo, "feat: published code")
    assert _run(repo, denylist, "range", excluded, published) == public_gate.EXIT_FINDINGS
