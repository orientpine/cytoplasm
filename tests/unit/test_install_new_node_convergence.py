"""A new node's first convergence — and its second run.

Three defects share one shape: nothing exercised the installer against a host it had
never converged, so each is invisible until someone runs it on a clean machine. All three
were found that way on 2026-09-07 by a third-party installation, not by a check
(``docs/troubleshooting/신규-노드-설치-공백.md`` §2 and §4).

* The reconcile unit requires ``/etc/autophagy/repair-approval.env`` with no ``-`` prefix,
  and the installer never created it, so ``EnableTimer`` failed.
* ``_timer`` started that unit before enabling its timer, but the unit's WorkingDirectory
  is created by the FIRST convergence — a circular precondition that can only fail.
* ``_repository_origin`` runs git as the installer's own uid over ops-owned checkouts and
  loses to dubious-ownership protection; the resulting ``None`` is misread as "no
  repository", driving a bare ``git clone`` into a populated directory on every re-run.

The credential file carries operator secrets, so its contract is asymmetric on purpose:
created when absent, never rewritten when present. A content-digest ``EnsureFile`` would
converge it back to the shipped template and silently destroy the tokens.
"""
from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from typing import Final

import pytest

from automation.install import state as install_state
from automation.install.apply import SystemMutator
from automation.install.assets import build_inputs
from automation.install.plan import (
    EnableTimer,
    EnsureFile,
    EnsureRepository,
    FileState,
    InstallInputs,
    SystemState,
    build_plan,
)
from automation.node_config import default_node_config

_REPO: Final = Path(__file__).resolve().parents[2]
_CREDENTIAL: Final = Path("/etc/autophagy/repair-approval.env")
_RECONCILE_TIMER: Final = "autophagy-deploy-reconcile.timer"
_RECONCILE_SERVICE: Final = "autophagy-deploy-reconcile.service"
_ORIGIN: Final = "https://github.com/orientpine/autophagy-agents.git"


def _update_trust_key() -> str:
    algorithm = b"ssh-ed25519"
    blob = len(algorithm).to_bytes(4, "big") + algorithm
    blob += (32).to_bytes(4, "big") + bytes(range(32))
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} first-run"


def _inputs() -> InstallInputs:
    return build_inputs(_REPO, default_node_config(), _update_trust_key())


def _credential_spec(inputs: InstallInputs):
    for spec in inputs.files:
        if spec.path == _CREDENTIAL:
            return spec
    raise AssertionError(f"the installer plans no {_CREDENTIAL}")


class _RecordingMutator(SystemMutator):
    """Runs the real dispatch and records the commands instead of issuing them."""

    def __init__(self, config) -> None:
        super().__init__(config)
        self.commands: list[tuple[str, ...]] = []

    def run(self, command, **_kwargs):  # type: ignore[override]
        self.commands.append(tuple(command))
        return None


# --------------------------------------------------------------------------- #
# The owner-notice credential the reconcile unit requires.
# --------------------------------------------------------------------------- #
def test_the_reconcile_unit_requires_the_credential_file_unconditionally() -> None:
    # Given: the unit the installer enables on every node.
    unit = (_REPO / "automation" / "systemd" / "autophagy-deploy-reconcile.service").read_text(
        encoding="utf-8"
    )

    # Then: it is a hard requirement, so an absent file is a startup failure, not a warning.
    assert "EnvironmentFile=/etc/autophagy/repair-approval.env" in unit
    assert "EnvironmentFile=-/etc/autophagy/repair-approval.env" not in unit


def test_the_installer_creates_the_credential_file_the_unit_requires() -> None:
    # Given: the assets a fresh install would place.
    config = default_node_config()
    spec = _credential_spec(_inputs())

    # Then: root writes it, the ops-run units read it, and nobody else can.
    assert spec.mode == 0o640
    assert spec.owner == "root"
    assert spec.group == config.ops_account

    # And: it names the keys owner_notice actually reads, so an operator filling it in
    # does not have to read the source to learn what belongs there.
    for key in ("DISCORD_BOT_TOKEN", "AUTOPHAGY_OWNER_ID", "OWNER_NOTICE_CHANNEL_ID"):
        assert key in spec.content


def test_an_absent_credential_file_is_created() -> None:
    # Given: a host that has never been converged.
    inputs = _inputs()

    # When: the first plan is built.
    plan = build_plan(inputs, SystemState.empty())

    # Then: the file is part of it.
    created = [
        action.spec.path for action in plan.actions if isinstance(action, EnsureFile)
    ]
    assert _CREDENTIAL in created


def test_an_operator_filled_credential_file_is_never_rewritten() -> None:
    # Given: the operator has entered real tokens, so the content no longer matches the
    # shipped template.
    inputs = _inputs()
    spec = _credential_spec(inputs)
    filled = FileState("operator-entered-content", spec.mode, spec.owner, spec.group)

    # When: the installer runs again over that host.
    plan = build_plan(inputs, SystemState(files={_CREDENTIAL: filled}))

    # Then: it does not converge the file back to the template and destroy them.
    rewritten = [
        action.spec.path for action in plan.actions if isinstance(action, EnsureFile)
    ]
    assert _CREDENTIAL not in rewritten


# --------------------------------------------------------------------------- #
# The circular pre-start.
# --------------------------------------------------------------------------- #
def test_enabling_the_reconcile_timer_does_not_start_its_service_first() -> None:
    # Given: the timer whose unit needs a release tree the first convergence creates.
    mutator = _RecordingMutator(default_node_config())

    # When: the installer enables it.
    _ = mutator.apply(EnableTimer(_RECONCILE_TIMER))

    # Then: it is enabled, and nothing tries to run the service before that tree exists —
    # the timer's own tick performs the first convergence.
    assert ("systemctl", "start", _RECONCILE_SERVICE) not in mutator.commands
    assert ("systemctl", "enable", "--now", _RECONCILE_TIMER) in mutator.commands


# --------------------------------------------------------------------------- #
# Dubious ownership, and the re-run it breaks.
# --------------------------------------------------------------------------- #
def _checkout_git_treats_as_another_users(tmp_path: Path, monkeypatch) -> Path:
    """A checkout git refuses as another user's — or a spoken skip when it cannot pretend.

    ``GIT_TEST_ASSUME_DIFFERENT_OWNER`` is a git test hook, and a build that ignores it
    cannot reproduce the production condition at all: setup succeeds and the command fails
    for an unrelated reason instead. A test that "passes" on such a build proves nothing
    about the fix, so this says so out loud rather than either failing or passing quietly.
    Measured 2026-09-07: reproduced on git 2.43.0 locally, ignored on the CI runner.

    The harness assertion lives HERE, in front of the test that depends on it, because an
    anti-vacuity check placed beside its subject can be skipped independently of it.
    """
    checkout = tmp_path / "mirror"
    _ = subprocess.run(("git", "init", "-q", str(checkout)), check=True)
    _ = subprocess.run(
        ("git", "-C", str(checkout), "remote", "add", "origin", _ORIGIN), check=True
    )
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    probe = subprocess.run(
        ("git", "-C", str(checkout), "remote", "get-url", "origin"),
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode == 0:
        pytest.skip("this git ignores GIT_TEST_ASSUME_DIFFERENT_OWNER; refusal not reproducible")
    assert "dubious ownership" in probe.stderr, probe.stderr
    return checkout


def test_an_ops_owned_checkout_still_resolves_its_origin(
    tmp_path: Path, monkeypatch
) -> None:
    # Given: a converged checkout git treats as another account's, which is what the
    # installer meets when it runs as root over the ops-owned mirror.
    checkout = _checkout_git_treats_as_another_users(tmp_path, monkeypatch)

    # When: the installer reads its origin to decide whether the repository is converged.
    resolved = install_state._repository_origin(checkout)

    # Then: it sees the real origin. Reporting None here is not a harmless unknown — the
    # planner reads it as "no repository" and plans a clone over a populated directory.
    assert resolved == _ORIGIN


def test_an_existing_checkout_is_repointed_rather_than_recloned(tmp_path: Path) -> None:
    # Given: a checkout that is already present, as on every re-run.
    checkout = tmp_path / "deploy"
    (checkout / ".git").mkdir(parents=True)
    mutator = _RecordingMutator(default_node_config())

    # When: the installer converges the repository action anyway.
    _ = mutator.apply(EnsureRepository(checkout, _ORIGIN, tmp_path / "id_ed25519"))

    # Then: it repoints the remote instead of cloning into a non-empty directory.
    issued = [" ".join(command) for command in mutator.commands]
    assert not any(" clone " in f" {command} " for command in issued), issued
    assert any("remote" in command and "set-url" in command for command in issued), issued
