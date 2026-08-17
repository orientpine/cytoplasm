"""One reconciliation tick, wired to the node. Runs as ops from a systemd timer.

MD-2. The decision lives in `deploy_reconcile`; this file owns only the edges — reading
the two shas, calling the privileged helper, persisting state, handing a notice to a
notifier. Keeping them apart is what lets "exactly one owner notice per incident" be a
unit test instead of a hope.

The notifier is deliberately still a seam. Whether the existing Ops bot DM path can
carry incident notices is unmeasured, and guessing would be worse than queueing: an
unconfigured notifier reports failure, `reconcile_tick` retains the notice in state, and
the next tick retries it. Nothing is lost, and the tick still converges — which is the
part that keeps prod correct. Wiring it is a separate, measured step (plan MD-2).
"""
from __future__ import annotations

import shlex
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from automation.deploy_reconcile import reconcile_tick
from automation.deploy_reconcile_state import DEFAULT_STATE_PATH, load_state, save_state
from automation.deploy_update_channel import (
    UpdateChannelSource,
    read_roster_update_channel,
    save_update_channel_binding as _save_update_channel_binding,
    with_update_channel,
)
from automation.node_config import load_node_config
from automation.node_config_state import unconfigured_reason
from automation.owner_notice import notify_owner
from automation.release_rollback import (
    Command,
    ReleaseEffects,
    ReleaseRuntime,
    ReleaseTransition,
    apply_release_update,
)
from automation.update_trust import UpdateTrustError, resolve_update_target
from automation.update_trust_state import release_floor_path

#: The ONLY privileged command this tick may run. No arguments: the helper resolves
#: the configured signed-update policy itself, so there is nothing here for a caller to aim.
CONVERGE_HELPER: Final = "/usr/local/libexec/autophagy-converge-origin-main"

_NODE_CONFIG: Final = load_node_config()
MIRROR: Final = _NODE_CONFIG.deploy_checkout
RELEASE_POINTER: Final = _NODE_CONFIG.release_current
UPDATE_CHANNEL_STATE: Final = (
    _NODE_CONFIG.private_root / "deploy-reconcile" / "update-channel.json"
)
#: The anti-rollback anchor (C1). Shared verbatim with the root-owned helper, which
#: derives it from the same ops node configuration — one floor, both verifiers.
RELEASE_FLOOR: Final = release_floor_path(_NODE_CONFIG)
_UPDATE_CHANNEL_SOURCE: Final = UpdateChannelSource(
    roster_path=_NODE_CONFIG.agent_home / ".hermes" / "roster.yaml",
    agent_account=_NODE_CONFIG.agent_account,
)
_RELEASE_RUNTIME: Final = ReleaseRuntime(
    current=RELEASE_POINTER,
    store_root=_NODE_CONFIG.service_root,
    failed_state=_NODE_CONFIG.private_root / "deploy-reconcile" / "failed-release.json",
    release_helper=_NODE_CONFIG.libexec_dir / "autophagy-install-release",
    gateway_helper=_NODE_CONFIG.libexec_dir / "autophagy-gateway-pair",
    smoke_script=RELEASE_POINTER / "automation" / "deploy-smoke.sh",
)

_LS_REMOTE_TIMEOUT: Final = 30.0
_CONVERGE_TIMEOUT: Final = 900.0
_VERDICT_TIMEOUT: Final = 60.0
_GIT_TIMEOUT: Final = 30.0
_FF_PULL_TIMEOUT: Final = 120.0

#: The mirror verdict comes from the shell primitive healthcheck.sh and land.sh already
#: share. Giving this — the only path that WRITES to the checkout — its own private idea
#: of "safe to move" is how the next fault class added to the verdict would fail to reach
#: the one caller that must respect it.
_MIRROR_PROBE: Final = Path(__file__).resolve().parent / "checkout_mirror_probe.sh"
_BEHIND: Final = "mirror-behind"

MIRROR_IN_SYNC: Final = "in-sync"
MIRROR_PULLED: Final = "fast-forwarded"
MIRROR_PULL_FAILED: Final = "ff-pull-failed"
MIRROR_PROD_STALE: Final = "left-behind"


def converge_command() -> tuple[str, ...]:
    return ("sudo", "-n", CONVERGE_HELPER)


def current_release_sha(pointer: Path = RELEASE_POINTER) -> str:
    """The generation prod is actually running, or "" if there is not one.

    Absent and dangling both read as "not converged". Treating a broken pointer as a
    satisfied one would make the reconciler go quiet exactly when the node is damaged.
    """
    try:
        return pointer.resolve(strict=True).name
    except OSError:
        return ""


def origin_main_sha(mirror: Path = MIRROR) -> str:
    """Ask origin directly. The mirror's own refs may be behind and are not the target."""
    try:
        completed = subprocess.run(
            ("git", "-C", str(mirror), "ls-remote", "origin", "refs/heads/main"),
            capture_output=True, text=True, check=False, timeout=_LS_REMOTE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    head = completed.stdout.split(maxsplit=1)
    return head[0] if head else ""


def candidate_update_sha(update_channel: str | None = None) -> str:
    """Resolve the same policy-bound target the root-owned helper independently verifies."""
    if update_channel is not None:
        return resolve_update_target(
            MIRROR,
            _NODE_CONFIG.require_signed_updates,
            remote_url=update_channel,
            floor_path=RELEASE_FLOOR,
        )
    return resolve_update_target(
        MIRROR,
        _NODE_CONFIG.require_signed_updates,
        floor_path=RELEASE_FLOOR,
    )


def run_converge(command: Sequence[str] | None = None) -> int:
    """Return the helper's exit code. 5 means another convergence holds the lock."""
    try:
        return subprocess.run(
            tuple(command or converge_command()),
            capture_output=True, text=True, check=False, timeout=_CONVERGE_TIMEOUT,
        ).returncode
    except (OSError, subprocess.SubprocessError):
        return 1

def _run(command: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str] | None:
    """None means the command could not be run at all — never an outcome to act on."""
    try:
        return subprocess.run(
            tuple(command), capture_output=True, text=True, check=False, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def roster_update_channel(path: Path | None = None) -> str | None:
    return read_roster_update_channel(_UPDATE_CHANNEL_SOURCE, path)


def persist_update_channel_binding(update_channel: str | None, path: Path) -> None:
    _save_update_channel_binding(update_channel, path)


def _run_release_command(command: Command, timeout: float) -> int:
    completed = _run(command, timeout)
    return 1 if completed is None else completed.returncode


def run_release_update(
    origin_sha: str,
    prior_sha: str,
    command: Sequence[str] | None = None,
) -> int:
    """Run the fixed converger and its mandatory post-transition transaction."""
    return apply_release_update(
        ReleaseTransition(prior_sha=prior_sha, target_sha=origin_sha),
        _RELEASE_RUNTIME,
        ReleaseEffects(
            converge=lambda: run_converge(command),
            run=_run_release_command,
            notify=notify_owner,
        ),
    )


def mirror_verdict(
    mirror: Path = MIRROR,
    probe: Path = _MIRROR_PROBE,
    *,
    update_channel: str | None = None,
) -> str:
    """The shared shell verdict for the checkout, or "" when it could not be taken."""
    source, target = shlex.quote(str(probe)), shlex.quote(str(mirror))
    completed = _run(
        with_update_channel(
            ("bash", "-c", f"source {source} && checkout_mirror_verdict {target}"),
            update_channel,
        ),
        _VERDICT_TIMEOUT,
    )
    return "" if completed is None else completed.stdout.strip()


def sync_mirror(
    origin_sha: str,
    *,
    mirror: Path = MIRROR,
    pointer: Path = RELEASE_POINTER,
    probe: Path = _MIRROR_PROBE,
    update_channel: str | None = None,
) -> str:
    """Carry the observation checkout forward. Best effort, and deliberately narrow.

    Converging the release never moves this checkout's HEAD — the snapshot is built in a
    detached worktree — so before this every landing left it behind until a human ran
    ``land.sh``, which branch work reaching main by PR never does.

    Two refusals are the point of the function:
    * anything but ``mirror-behind`` is left exactly as found. Dirty or ahead means work
      that exists nowhere else (2026-07-27 선례), and a timer that resolved it would be
      the destructive repair ``checkout_mirror_guidance`` exists to forbid.
    * while prod has not reached origin/main, the lag IS the healthcheck's evidence of a
      stale release. Erasing it would make a broken convergence look like a healthy node.
    """
    if current_release_sha(pointer) != origin_sha:
        return f"{MIRROR_PROD_STALE}: prod has not reached origin/main yet"
    head = _run(("git", "-C", str(mirror), "rev-parse", "HEAD"), _GIT_TIMEOUT)
    if head is not None and head.returncode == 0 and head.stdout.strip() == origin_sha:
        return MIRROR_IN_SYNC
    verdict = mirror_verdict(mirror, probe, update_channel=update_channel)
    if verdict != _BEHIND:
        return f"untouched: {verdict or 'verdict unavailable'}"
    pulled = _run(
        with_update_channel(
            ("git", "-C", str(mirror), "pull", "--ff-only"),
            update_channel,
        ),
        _FF_PULL_TIMEOUT,
    )
    if pulled is None or pulled.returncode != 0:
        return MIRROR_PULL_FAILED
    return MIRROR_PULLED


def main() -> int:
    # Fail loudly rather than converge on a guess. An unconfigured node cannot reach
    # its own origin, and the old behaviour was to skip with rc 0 forever — the state
    # file kept saying everything was fine while production sat frozen.
    unset = unconfigured_reason(_NODE_CONFIG)
    if unset is not None:
        print(f"[deploy-reconcile] NODE-CONFIG-UNSET {unset}", file=sys.stderr)
        return 1
    update_channel = roster_update_channel()
    try:
        target_sha = (
            candidate_update_sha()
            if update_channel is None
            else candidate_update_sha(update_channel)
        )
    except UpdateTrustError as error:
        print(f"[deploy-reconcile] UPDATE-TRUST-BLOCK {error} — skipping tick", file=sys.stderr)
        return 0
    if not target_sha:
        # Transport trouble is not drift. Converging on an unknown target, or reporting
        # an incident because github was briefly unreachable, are both worse than
        # waiting two minutes for the next tick.
        print("[deploy-reconcile] update target unresolved — skipping tick", file=sys.stderr)
        return 0
    try:
        persist_update_channel_binding(update_channel, UPDATE_CHANNEL_STATE)
    except OSError as error:
        print(
            f"[deploy-reconcile] UPDATE-CHANNEL-BINDING-BLOCK {error} — skipping tick",
            file=sys.stderr,
        )
        return 0
    state = load_state(DEFAULT_STATE_PATH)
    current_sha = current_release_sha()
    updated = reconcile_tick(
        state,
        origin_sha=target_sha,
        current_sha=current_sha,
        now=time.time(),
        converge=lambda: run_release_update(target_sha, current_sha),
        deliver=notify_owner,
    )
    save_state(DEFAULT_STATE_PATH, updated)
    # After the state is durable, and never able to change this tick's outcome: the
    # observation post is not prod, so nothing about it may fail a reconciliation.
    mirror_result = (
        sync_mirror(target_sha)
        if update_channel is None
        else sync_mirror(target_sha, update_channel=update_channel)
    )
    print(f"[deploy-reconcile] mirror {mirror_result}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
