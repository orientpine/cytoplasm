"""Regression: git clone must never read a config URL as an option or helper.

F3 (security audit, 2026-08-15). Four sites ran ``git clone <url> <dest>`` with
no ``--`` separator and no scheme allowlist:

* ``automation/install/apply.py`` (``origin_url`` from node.toml)
* ``automation/managed_sync/fetch.py`` (``remote_url``)
* ``automation/rag_ingest/sources/obsidian.py`` (``repo_url``)
* ``automation/obsidian_write/writer.py`` (``repo_url``)

A value beginning with ``-`` was consumed by git as an option
(``--upload-pack=/bin/sh``), and ``ext::sh -c ...`` selected git's
command-executing transport helper. Each site now validates through the shared
``automation.git_remote_url`` helper and passes ``--`` before the URL.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest

from automation.git_remote_url import GitRemoteUrlError, validate_remote_url
from automation.install.apply import SystemMutator
from automation.install.plan import EnsureRepository
from automation.managed_sync.fetch import ManagedFetchError, sync_remote
from automation.node_config import default_node_config
from automation.obsidian_write.config import ObsidianWriteConfig, ObsidianWriteError
from automation.obsidian_write.note import NotePlan
from automation.obsidian_write.writer import write_note
from automation.rag_ingest.config import ObsidianSourceConfig
from automation.rag_ingest.sources.obsidian import ObsidianSyncError, sync_mirror

# The audit's payloads.
OPTION_INJECTION = "--upload-pack=/bin/sh"
EXT_TRANSPORT = "ext::sh -c 'touch /tmp/PWNED'"

UNSAFE_URLS = [
    OPTION_INJECTION,
    "-oProxyCommand=/bin/sh",
    EXT_TRANSPORT,
    "EXT::sh -c id",
    "ext::whoami",
    "ssh://host/repo.git\ninjected",
    "",
    "http://insecure.example/repo.git",
    "relative/path/repo.git",
]

SAFE_URLS = [
    "ssh://git@git.example.invalid/team/project.git",
    "https://github.example/owner/repo.git",
    "git@github.example:owner/repo.git",
    "member@fixture:/srv/fixtures/remote.git",
    "file:///srv/mirrors/repo.git",
    "/srv/mirrors/repo.git",
]


@dataclass(frozen=True, slots=True)
class Captured:
    argv: tuple[str, ...]


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[Captured] = []

    def __call__(
        self,
        args: list[str],
        /,
        *,
        cwd: Path | None = None,
        env: dict[str, str],
        capture_output: bool = False,
        text: bool = False,
        timeout: float = 0.0,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(Captured(tuple(args)))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


class RagIngestRunner:
    def __init__(self) -> None:
        self.calls: list[Captured] = []

    def __call__(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str],
        timeout: float = 0.0,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(Captured(tuple(args)))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


@dataclass(frozen=True, slots=True)
class _SyncConfig:
    remote_url: str
    mirror_dir: Path
    ssh_key_path: Path


def _clone_argv(calls: list[Captured]) -> tuple[str, ...]:
    clones = [call.argv for call in calls if call.argv[:2] == ("git", "clone")]
    assert len(clones) == 1, "expected exactly one clone invocation"
    return clones[0]


def _assert_separator_precedes_url(argv: tuple[str, ...], url: str) -> None:
    assert "--" in argv, "clone argv must carry an end-of-options separator"
    assert argv.index("--") + 1 == argv.index(url), "`--` must sit immediately before the URL"


# --------------------------------------------------------------------------
# The shared validator.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url", UNSAFE_URLS)
def test_validator_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(GitRemoteUrlError):
        _ = validate_remote_url(url)


@pytest.mark.parametrize("url", SAFE_URLS)
def test_validator_accepts_legitimate_urls(url: str) -> None:
    assert validate_remote_url(url) == url


def test_validator_does_not_mistake_an_ipv6_literal_for_a_helper() -> None:
    # `::` inside brackets is an address, not a transport helper prefix.
    assert validate_remote_url("ssh://[::1]/repo.git") == "ssh://[::1]/repo.git"


# --------------------------------------------------------------------------
# Site 1 — the installer.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url", [OPTION_INJECTION, EXT_TRANSPORT])
def test_installer_repository_refuses_an_unsafe_origin(tmp_path: Path, url: str) -> None:
    mutator = SystemMutator(default_node_config())

    with pytest.raises(OSError):
        mutator.apply(EnsureRepository(tmp_path / "checkout", url, tmp_path / "key"))


def test_installer_repository_separates_options_from_the_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "ssh://git@git.example.invalid/team/project.git"
    runner = RecordingRunner()
    mutator = SystemMutator(default_node_config())

    def fake_run(
        command: tuple[str, ...],
        *,
        env: dict[str, str] | None = None,
        _cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return runner(list(command), env=env or {})

    monkeypatch.setattr(mutator, "run", fake_run)

    mutator.apply(EnsureRepository(tmp_path / "checkout", url, tmp_path / "key"))

    # The installer wraps git in runuser/env, so locate the clone verb itself.
    argv = runner.calls[0].argv
    clone = argv.index("clone")
    assert argv[clone - 1] == "git"
    assert argv[clone + 1] == "--"
    assert argv[clone + 2] == url


# --------------------------------------------------------------------------
# Site 2 — managed-sync.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url", [OPTION_INJECTION, EXT_TRANSPORT])
def test_managed_sync_refuses_an_unsafe_remote(tmp_path: Path, url: str) -> None:
    config = _SyncConfig(url, tmp_path / "mirror", tmp_path / "key")

    with pytest.raises(ManagedFetchError):
        _ = sync_remote(config, RecordingRunner())


def test_managed_sync_separates_options_from_the_url(tmp_path: Path) -> None:
    url = "ssh://feed.example/managed-skills.git"
    runner = RecordingRunner()

    _ = sync_remote(_SyncConfig(url, tmp_path / "mirror", tmp_path / "key"), runner)

    _assert_separator_precedes_url(_clone_argv(runner.calls), url)


# --------------------------------------------------------------------------
# Site 3 — rag-ingest obsidian mirror.
# --------------------------------------------------------------------------


def _obsidian_source(tmp_path: Path, url: str) -> ObsidianSourceConfig:
    return ObsidianSourceConfig(
        enabled=True,
        repo_url=url,
        mirror_dir=tmp_path / "mirror",
        ssh_key_path=tmp_path / "key",
        sensitivity_rules_path=tmp_path / "sensitivity.yaml",
    )


@pytest.mark.parametrize("url", [OPTION_INJECTION, EXT_TRANSPORT])
def test_rag_ingest_refuses_an_unsafe_repo_url(tmp_path: Path, url: str) -> None:
    with pytest.raises(ObsidianSyncError):
        _ = sync_mirror(_obsidian_source(tmp_path, url), RagIngestRunner())


def test_rag_ingest_separates_options_from_the_url(tmp_path: Path) -> None:
    url = "git@example.invalid:cha/vault.git"
    runner = RagIngestRunner()

    _ = sync_mirror(_obsidian_source(tmp_path, url), runner)

    _assert_separator_precedes_url(_clone_argv(runner.calls), url)


# --------------------------------------------------------------------------
# Site 4 — the obsidian write clone.
# --------------------------------------------------------------------------


def _write_config(tmp_path: Path, url: str) -> ObsidianWriteConfig:
    key = tmp_path / "key"
    _ = key.write_text("not-a-real-key\n", encoding="utf-8")
    return ObsidianWriteConfig(
        repo_url=url,
        clone_dir=tmp_path / "write-clone",
        ssh_key_path=key,
        branch="main",
    )


@pytest.mark.parametrize("url", [OPTION_INJECTION, EXT_TRANSPORT])
def test_obsidian_write_refuses_an_unsafe_repo_url(tmp_path: Path, url: str) -> None:
    plan = NotePlan(PurePosixPath("2_Areas/note.md"), "note", "body")
    runner = RecordingRunner()

    with pytest.raises(ObsidianWriteError):
        _ = write_note(plan, _write_config(tmp_path, url), runner)

    assert not [call for call in runner.calls if call.argv[:2] == ("git", "clone")]


def test_obsidian_write_separates_options_from_the_url(tmp_path: Path) -> None:
    url = "git@example.invalid:cha/vault.git"
    plan = NotePlan(PurePosixPath("2_Areas/note.md"), "note", "body")
    runner = RecordingRunner()

    with pytest.raises(ObsidianWriteError):
        # The fake runner never materializes a clone, so a later step fails —
        # the clone argv has already been captured by then.
        _ = write_note(plan, _write_config(tmp_path, url), runner)

    _assert_separator_precedes_url(_clone_argv(runner.calls), url)
