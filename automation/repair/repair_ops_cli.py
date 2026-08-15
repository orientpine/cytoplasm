"""Ops-only entry point for owner-gated W6-2 repairs."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from automation.interop.injection_adapter import InboundEvent
from automation.node_config import load_node_config
from automation.regression_bank.bank_state import DEFAULT_STATE_PATH
from automation.repair.repair_ops_adapters import CodexPlanner, LiteLLMPlanner, PeerSandbox, StaticPlanner, private_log
from automation.repair.repair_ops_approval import ApprovalReaction, ManualOwnerApproval, SignedOwnerApproval, manual_approval_text
from automation.repair.repair_ops_core import Approval, RepairAgent, RepairOutcome, RepairPhase
from automation.repair.repair_ops_discord import RepairDiscordError, configured_discord
from automation.repair.repair_ops_git import GitRepository, RepairOpsError
from automation.repair.repair_ops_pending import PendingRepairApprovalStore
from automation.repair.repair_ops_posting import PostingOwnerApproval
from automation.repair.repair_ops_reaction_watch import ReactionDecision, reaction_decision
from automation.repair.repair_lifecycle import LifecycleState, RepairLifecycleStore
from automation.repair.repair_ops_reporting import HermesTicketBoard, PatchDocumentWriter
from automation.repair.repair_redaction import redact
from automation.repair.repair_ops_work_clone import RepairWorkClone


# Not under /home: both repair units run with ProtectHome=yes, which would make
# the key invisible to the service even though the file exists on disk.
DEFAULT_PUSH_KEY = Path("/srv/autophagy-private/repair_push_key")
# Same trap one layer down: ssh resolves ~/.ssh/known_hosts through the passwd
# entry, and the node has no /etc/ssh/ssh_known_hosts, so a pinned file is the
# only host-key database the sandboxed unit can reach.
DEFAULT_KNOWN_HOSTS = Path("/srv/autophagy-private/repair_known_hosts")


@dataclass(frozen=True, slots=True)
class RepairOpsConfig:
    """Typed process configuration; all operational inputs originate in the ops environment."""

    ticket_id: str
    checkout: Path
    logs: Path
    plans: Path
    approval_log: Path
    event: InboundEvent | None
    signature: str | None
    work_clone: Path = Path("/srv/autophagy-repair-work")


def _event_from_environment() -> InboundEvent | None:
    """Build the synthetic event only when all signed-injection fields are supplied."""
    event_id = os.environ.get("REPAIR_E2E_EVENT_ID")
    user_id = os.environ.get("REPAIR_E2E_USER_ID")
    channel_id = os.environ.get("REPAIR_E2E_CHANNEL_ID")
    text = os.environ.get("REPAIR_E2E_TEXT")
    if event_id is None and user_id is None and channel_id is None and text is None:
        return None
    if event_id is None or user_id is None or channel_id is None or text is None:
        return None
    return InboundEvent(event_id, user_id, channel_id, text)


def _config(argv: list[str]) -> RepairOpsConfig:
    """Parse the one ticket argument while keeping paths and credentials environment-owned."""
    if len(argv) != 1:
        raise SystemExit("usage: repair_ops_cli.py TICKET_ID")
    return RepairOpsConfig(
        argv[0],
        Path(os.environ.get("REPAIR_CHECKOUT", "/srv/autophagy-agents")),
        Path(os.environ.get("REPAIR_LOG_ROOT", "/srv/autophagy-private/repair-logs")),
        Path(os.environ.get("REPAIR_PLAN_ROOT", "/srv/autophagy-private/repair-plans")),
        Path(os.environ.get("REPAIR_APPROVAL_LOG", "/srv/autophagy-private/repair-approvals.jsonl")),
        _event_from_environment(),
        os.environ.get("REPAIR_E2E_SIGNATURE"),
        Path(os.environ.get("REPAIR_WORK_CLONE", "/srv/autophagy-repair-work")),
    )


def _approval(config: RepairOpsConfig) -> Approval | None:
    e2e_mode = os.environ.get("E2E_TEST_MODE")
    e2e_inputs = config.event is not None or config.signature is not None or os.environ.get("REPAIR_E2E_SECRET") is not None
    if e2e_mode == "1":
        if config.event is None or config.signature is None or os.environ.get("REPAIR_E2E_SECRET") is None:
            return None
        try:
            secret = bytes.fromhex(os.environ["REPAIR_E2E_SECRET"])
        except ValueError:
            return None
        return SignedOwnerApproval(os.environ["AUTOPHAGY_OWNER_ID"], config.approval_log, config.event, config.signature, secret, True)
    if e2e_mode is not None or e2e_inputs:
        return None
    try:
        discord = configured_discord()
    except RepairDiscordError:
        return None
    return PostingOwnerApproval(
        discord.owner_id,
        PendingRepairApprovalStore(_pending_root()),
        discord,
        lambda: datetime.now(UTC),
        binding=discord.binding,
    )


def _pending_root() -> Path:
    return Path(os.environ.get("REPAIR_APPROVAL_PENDING_ROOT", "/srv/autophagy-private/repair-approval-pending"))


def planner_for(config: RepairOpsConfig) -> CodexPlanner | LiteLLMPlanner | StaticPlanner:
    provider = os.environ.get("REPAIR_DIAGNOSIS_PROVIDER")
    if provider == "static-e2e" and os.environ.get("E2E_TEST_MODE") == "1":
        return StaticPlanner(config.plans)
    if provider == "openai-codex":
        return CodexPlanner(config.plans)
    return LiteLLMPlanner(config.plans, Path(os.environ.get("REPAIR_LITELLM_KEY_FILE", "/home/ops/.config/autophagy/ops-repair.key")))


def _agent(config: RepairOpsConfig, approval: Approval) -> RepairAgent:
    work_clone = RepairWorkClone(config.checkout, config.work_clone).prepare()
    return RepairAgent(
        planner_for(config),
        PeerSandbox(load_node_config().primary_node_name, config.checkout),
        approval,
        GitRepository(
            work_clone,
            Path(os.environ.get("REPAIR_BANK_STATE", str(DEFAULT_STATE_PATH))),
        ),
        HermesTicketBoard(),
        PatchDocumentWriter(work_clone / "docs/patch"),
        RepairLifecycleStore(Path(os.environ.get("REPAIR_STATE_ROOT", "/srv/autophagy-private/repair-state"))),
    )


def _push_repair_branch(config: RepairOpsConfig, outcome: RepairOutcome) -> str | None:
    """Publish a committed repair as ``repair/<ticket>`` for the owner to merge.

    Without this the commit lives only in the work clone, which the next run
    resets to origin/main — the repair would be silently lost. Nothing is
    published when no commit was made, and never during E2E runs.
    """
    if outcome.commit is None or outcome.phase is RepairPhase.BANK_BLOCKED:
        return None
    if os.environ.get("E2E_TEST_MODE") is not None:
        return None
    key = Path(os.environ.get("REPAIR_PUSH_KEY", str(DEFAULT_PUSH_KEY)))
    if not key.is_file():
        # Falling through would let git pick the ops key, which is read-only by
        # design: the push would fail anyway, but far from its real cause.
        raise RepairOpsError(f"repair push key is missing at {key}")
    known_hosts = Path(os.environ.get("REPAIR_KNOWN_HOSTS", str(DEFAULT_KNOWN_HOSTS)))
    if not known_hosts.is_file():
        raise RepairOpsError(f"repair known_hosts is missing at {known_hosts}")
    return RepairWorkClone(config.checkout, config.work_clone).push_branch(
        config.ticket_id, ssh_key=key, known_hosts=known_hosts
    )


def _run(config: RepairOpsConfig, approval: Approval) -> int:
    outcome = _agent(config, approval).repair(config.ticket_id, private_log(config.ticket_id, config.logs))
    branch: str | None = None
    push_error: str | None = None
    try:
        branch = _push_repair_branch(config, outcome)
    except RepairOpsError as error:
        push_error = redact(str(error))[:180]
    print(json.dumps({"ticket": outcome.ticket_id, "phase": outcome.phase, "commit": outcome.commit, "patch_doc": str(outcome.patch_doc) if outcome.patch_doc else None, "branch": branch, "push_error": push_error}))
    if outcome.phase is RepairPhase.BANK_BLOCKED:
        return 3
    return 4 if push_error is not None else 0


def _apply_approved(config: RepairOpsConfig) -> int:
    if os.environ.get("E2E_TEST_MODE") is not None:
        return 2
    pending = PendingRepairApprovalStore(_pending_root()).get(config.ticket_id)
    if pending is None:
        return 1
    try:
        discord = configured_discord()
    except RepairDiscordError:
        return 2
    bound_discord = discord.for_pending(pending)
    if reaction_decision(pending, discord.owner_id, bound_discord) is not ReactionDecision.APPROVED:
        return 1
    reaction = ApprovalReaction(
        pending.message_id,
        discord.owner_id,
        bound_discord.binding.channel_id,
        manual_approval_text(pending.ticket_id, pending.action_hash),
        False,
    )
    return _run(config, ManualOwnerApproval(discord.owner_id, reaction, bound_discord.binding.channel_id))


def _discard(config: RepairOpsConfig, reason: str) -> int:
    if reason not in {"owner_cancelled", "approval_expired"}:
        return 2
    pending = PendingRepairApprovalStore(_pending_root()).get(config.ticket_id)
    if pending is None:
        return 1
    PendingRepairApprovalStore(_pending_root()).remove(config.ticket_id)
    lifecycle = RepairLifecycleStore(Path(os.environ.get("REPAIR_STATE_ROOT", "/srv/autophagy-private/repair-state")))
    _ = lifecycle.transition(config.ticket_id, LifecycleState.REOPENED, reason)
    HermesTicketBoard().reopen(config.ticket_id, reason)
    return 0


def main() -> int:
    """Run one ticket or one watcher-dispatched terminal action as the ops account."""
    argv = sys.argv[1:]
    if len(argv) == 2 and argv[0] == "--apply-approved":
        return _apply_approved(_config([argv[1]]))
    if len(argv) == 3 and argv[0] == "--discard-pending":
        return _discard(_config([argv[1]]), argv[2])
    config = _config(argv)
    approval = _approval(config)
    if approval is None:
        return 2
    return _run(config, approval)


if __name__ == "__main__":
    raise SystemExit(main())
