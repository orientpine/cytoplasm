"""``approval_lifecycle.ApprovalGate`` over a skill gate's pending record and binding.

Serves BOTH skill gates — ``skill-deploy:{skill}`` and ``skill-publish:{skill}`` —
so neither grows a parallel render/resolve/watch surface of its own.

L1  The pending record is written ONLY by :meth:`SkillApprovalGate.commit`.
L2  A stored ``message_id`` is never replaced: it is superseded (DELETE before
    drop) or left alone. No path here overwrites a live message id.
L3  A request the owner already decided (✅/⛔) is reported, never destroyed.
L4  Liveness that cannot be proven raises :class:`ApprovalSurfaceError`, and an
    unreadable record raises :class:`ApprovalRecordsError` — never "absent".
L6  Every read, react and delete takes its channel from the binding the record
    itself stores (SI-1) — the surface a message was posted to is the surface it
    is consumed on, whatever current policy says.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, Protocol, TypeAlias, assert_never
from urllib.error import HTTPError
from urllib.parse import quote

from automation.interop.approval_lifecycle import (
    ApprovalIntent,
    ApprovalRecordsError,
    ApprovalRequest,
    ApprovalSurfaceError,
    PostedApproval,
    Probe,
)
from automation.skill_gate_specs import APPROVE_EMOJI, CANCEL_EMOJI, GateSpec, StoredBinding
from automation.skill_gate_surface import ApprovalBindings, binding_of_post

_TRANSPORT_ERRORS: Final = (OSError, ValueError, KeyError, TypeError)
OwnerDecision: TypeAlias = Literal["approved", "denied", "absent"]

#: stderr prefix every binding refusal carries, so a journal names WHICH check said no.
REJECT_TOKEN: Final = "APPROVAL-BINDING-REJECT"
#: Sibling of ``pending/``: one 0600 copy per refused record, kept for reproduction.
PENDING_REJECTED_DIRNAME: Final = "pending-rejected"


class RejectCause(StrEnum):
    """One value per refusing branch of the execution-binding judgment — never shared.

    2026-08-29: fifteen resumes of ``skill-deploy:proposal`` produced one journal line,
    ``REJECTED: owner approval binding invalid``, for a dozen possible causes. A cause
    that cannot be told apart from eleven others is not diagnosable, so each branch
    below owns exactly one token.
    """

    RECORD_ABSENT = "record-absent"
    RECORD_UNREADABLE = "record-unreadable"
    LEGACY_BINDING_INCOMPLETE = "legacy-binding-incomplete"
    BINDING_UNREADABLE = "binding-unreadable"
    KEY_MISMATCH = "key-mismatch"
    SHA_MISMATCH = "sha-mismatch"
    MESSAGE_ID_MISMATCH = "message-id-mismatch"
    NONCE_MISMATCH = "nonce-mismatch"
    ACTION_MISMATCH = "action-mismatch"
    DESTINATION_MISMATCH = "destination-mismatch"
    CHANNEL_MISMATCH = "channel-mismatch"
    PENDING_NONCE_REUSED = "pending-nonce-reused"
    PENDING_RECORD_UNREADABLE = "pending-record-unreadable"
    APPROVAL_LOG_UNREADABLE = "approval-log-unreadable"
    APPROVAL_LOG_NONCE_REBOUND = "approval-log-nonce-rebound"
    SURFACE_UNVERIFIABLE = "surface-unverifiable"
    MESSAGE_MISSING = "message-missing"
    PROBE_BINDING_MISMATCH = "probe-binding-mismatch"
    PROBE_UNVERIFIABLE = "probe-unverifiable"
    OWNER_CANCELLED = "owner-cancelled"
    OWNER_REACTION_ABSENT = "owner-reaction-absent"


#: Every non-approving probe state maps to its own cause; ``APPROVED`` maps to none.
_PROBE_CAUSES: Final[Mapping[Probe, RejectCause]] = {
    Probe.MISSING: RejectCause.MESSAGE_MISSING,
    Probe.BINDING_MISMATCH: RejectCause.PROBE_BINDING_MISMATCH,
    Probe.UNVERIFIABLE: RejectCause.PROBE_UNVERIFIABLE,
    Probe.CANCELLED: RejectCause.OWNER_CANCELLED,
    Probe.BOUND_PENDING: RejectCause.OWNER_REACTION_ABSENT,
}


@dataclass(frozen=True, slots=True)
class BindingOutcome:
    """The rich verdict behind the outward bool: the state reached and why it refused.

    ``cause`` is ``None`` only for :data:`Probe.APPROVED`; every other outcome names
    the single branch that produced it, whether or not the caller demands ✅.
    """

    state: Probe | None
    cause: RejectCause | None

    def approved(self) -> bool:
        """The owner's current ✅ authorizes this exact execution."""
        return self.state is Probe.APPROVED

    def bound(self) -> bool:
        """The binding is current — approved, or still awaiting the owner's decision."""
        return self.state in (Probe.APPROVED, Probe.BOUND_PENDING)


def _refused(cause: RejectCause) -> BindingOutcome:
    return BindingOutcome(None, cause)


def _report(cause: RejectCause | None) -> None:
    if cause is not None:
        print(f"{REJECT_TOKEN}:{cause.value}", file=sys.stderr)


def preserve_rejected(gate: SkillApprovalGate, cause: RejectCause | None) -> Path | None:
    """Copy the refused pending record aside BEFORE a later supersede can delete it.

    2026-08-29: the record that would have explained fifteen refusals was gone by the
    time anyone looked. Best-effort by design — preservation is diagnostics, and a
    failure to write evidence must never convert a refusal into a crash.
    """
    if cause is None:
        return None
    try:
        doomed = gate.path().read_text(encoding="utf-8")
    except OSError:
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = (
        gate.surface.gate_dir
        / PENDING_REJECTED_DIRNAME
        / f"{gate.spec.record_name()}-{stamp}-{cause.value}.json"
    )
    try:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _ = target.write_text(doomed, encoding="utf-8")
        target.chmod(0o600)
    except OSError:
        return None
    return target


def owner_decision(reacted: Callable[[str], bool]) -> OwnerDecision:
    """Apply the repository-wide owner reaction precedence at one shared gate seam."""
    if reacted(CANCEL_EMOJI):
        return "denied"
    if reacted(APPROVE_EMOJI):
        return "approved"
    return "absent"


class DiscordApi(Protocol):
    """The gate's existing ``_api`` funnel — every request rides this one seam."""

    def __call__(self, method: str, path: str, payload: dict[str, str] | None = None, /) -> object: ...


@dataclass(frozen=True, slots=True)
class GateSurface:
    """Discord + runtime-state seam; the binding resolves lazily so a reuse never scans guilds."""

    api: DiscordApi
    gate_dir: Path
    owner_id: Callable[[], str]
    bindings: Callable[[], ApprovalBindings]



@dataclass(frozen=True, slots=True)
class ApprovalExecution:
    """The immutable content, nonce, action, and destination an execution presents."""

    request: ApprovalRequest
    nonce: str
    action: str
    destination: str


@dataclass(frozen=True, slots=True)
class SkillApprovalGate:

    """One live owner-approval message per gate key, bound to one pending record."""

    surface: GateSurface
    spec: GateSpec

    def path(self) -> Path:
        return self.surface.gate_dir / "pending" / f"{self.spec.record_name()}.json"

    def stored(self) -> dict[str, str] | None:
        """The pending record, or None when absent — unreadable is NEVER "absent"."""
        path = self.path()
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as error:
            raise ApprovalRecordsError(str(path)) from error
        if not isinstance(decoded, dict) or any(
            not isinstance(name, str) or not isinstance(value, str) for name, value in decoded.items()
        ):
            raise ApprovalRecordsError(str(path))
        return {str(name): str(value) for name, value in decoded.items()}

    def binding(self) -> StoredBinding | None:
        """The readable binding, including migration-only legacy state."""
        record = self.stored()
        if record is None:
            return None
        found = self.spec.stored(record)
        if found is None:
            raise ApprovalRecordsError(str(self.path()))
        return found

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        if key != self.spec.key():
            raise ApprovalRecordsError(key)
        record = self.stored()
        if record is None:
            return ()
        found = self.spec.stored(record)
        if found is None:
            raise ApprovalRecordsError(str(self.path()))
        return (
            ApprovalRequest(
                key=key,
                action_hash=found.action_hash,
                message_id=found.message_id,
                channel_id=self._channel_of(record),
                created_at="",
            ),
        )

    def _channel_of(self, record: Mapping[str, str]) -> str:
        """SI-1: the record's OWN binding decides where its message lives, not policy."""
        return self.surface.bindings().stored(record).channel_id

    def channel_id(self) -> str:
        """This gate's channel: the stored binding when a record exists, else a fresh one."""
        record = self.stored()
        if record is None:
            return self.surface.bindings().new().channel_id
        return self._channel_of(record)

    def new_record(self, posted: PostedApproval) -> dict[str, str]:
        """This run's record: the spec's fields plus the binding the post actually used."""
        binding = binding_of_post(self.surface.bindings().kind, posted.channel_id)
        return self.spec.new_record(posted.message_id, binding)

    def probe(self, request: ApprovalRequest) -> Probe:
        try:
            record = self.stored()
        except ApprovalRecordsError as error:
            raise ApprovalSurfaceError(str(error)) from error
        content = self._content(request)
        if content is None:
            return Probe.MISSING
        if record is None:
            return Probe.BINDING_MISMATCH
        found = self.spec.stored(record)
        if found is None:
            return Probe.BINDING_MISMATCH
        if not found.action_hash:
            if (
                request.key,
                request.action_hash,
                request.message_id,
                request.channel_id,
            ) != (
                self.spec.key(),
                "",
                found.message_id,
                self._channel_of(record),
            ):
                return Probe.BINDING_MISMATCH
        elif not self.spec.bound(content, record):
            return Probe.BINDING_MISMATCH
        match owner_decision(lambda emoji: self._owner_reacted(request, emoji)):
            case "denied":
                return Probe.CANCELLED
            case "approved":
                return Probe.APPROVED
            case "absent":
                return Probe.BOUND_PENDING
            case unreachable:
                assert_never(unreachable)

    def approval_outcome(self, execution: ApprovalExecution, approval_log: Path) -> BindingOutcome:
        """The rich verdict :meth:`valid_approval` reduces to a bool, one token per refusal."""
        outcome = self._binding_outcome(execution, approval_log)
        if not outcome.approved():
            _report(outcome.cause)
        return outcome

    def valid_approval(self, execution: ApprovalExecution, approval_log: Path) -> bool:
        """Accept only the current owner decision for one complete execution binding."""
        return self.approval_outcome(execution, approval_log).approved()

    def valid_binding(self, execution: ApprovalExecution, approval_log: Path) -> bool:
        """Accept a current undecided or approved binding for signed E2E owner injection."""
        outcome = self._binding_outcome(execution, approval_log)
        if not outcome.bound():
            _report(outcome.cause)
        return outcome.bound()

    def _binding_outcome(
        self, execution: ApprovalExecution, approval_log: Path
    ) -> BindingOutcome:
        request = execution.request
        try:
            record = self.stored()
        except (ApprovalRecordsError, ApprovalSurfaceError):
            return _refused(RejectCause.RECORD_UNREADABLE)
        if record is None:
            return _refused(RejectCause.RECORD_ABSENT)
        found = self.spec.stored(record)
        if found is None:
            return _refused(RejectCause.LEGACY_BINDING_INCOMPLETE)
        try:
            channel_id = self._channel_of(record)
        except (ApprovalRecordsError, ApprovalSurfaceError):
            return _refused(RejectCause.BINDING_UNREADABLE)
        for cause, stored, expected in (
            (RejectCause.KEY_MISMATCH, request.key, self.spec.key()),
            (RejectCause.SHA_MISMATCH, found.action_hash, request.action_hash),
            (RejectCause.MESSAGE_ID_MISMATCH, found.message_id, request.message_id),
            (RejectCause.NONCE_MISMATCH, found.nonce, execution.nonce),
            (RejectCause.ACTION_MISMATCH, record.get("approval_action", ""), execution.action),
            (
                RejectCause.DESTINATION_MISMATCH,
                record.get("approval_destination", ""),
                execution.destination,
            ),
            (RejectCause.CHANNEL_MISMATCH, channel_id, request.channel_id),
        ):
            if stored != expected:
                return _refused(cause)
        reused = self._pending_nonce_reused(execution.nonce)
        if reused is None:
            reused = self._approval_nonce_reused(execution, approval_log)
        if reused is not None:
            return _refused(reused)
        try:
            probe_result = self.probe(request)
        except ApprovalSurfaceError:
            return _refused(RejectCause.SURFACE_UNVERIFIABLE)
        return BindingOutcome(probe_result, _PROBE_CAUSES.get(probe_result))

    def _pending_nonce_reused(self, nonce: str) -> RejectCause | None:
        for candidate in self.path().parent.glob("*.json"):
            if candidate == self.path():
                continue
            try:
                decoded = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return RejectCause.PENDING_RECORD_UNREADABLE
            if not isinstance(decoded, dict):
                return RejectCause.PENDING_RECORD_UNREADABLE
            candidate_nonce = decoded.get(
                "deploy_nonce",
                decoded.get("publish_nonce", decoded.get("release_nonce")),
            )
            if candidate_nonce == nonce:
                return RejectCause.PENDING_NONCE_REUSED
        return None

    def _approval_nonce_reused(
        self, execution: ApprovalExecution, approval_log: Path
    ) -> RejectCause | None:
        try:
            lines = approval_log.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return None
        except OSError:
            return RejectCause.APPROVAL_LOG_UNREADABLE
        expected = {
            "action": execution.action,
            "action_hash": execution.request.action_hash,
            "deploy_nonce": execution.nonce,
            "destination": execution.destination,
            "message_id": execution.request.message_id,
        }
        for line in lines:
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError:
                continue
            binding = decoded.get("binding") if isinstance(decoded, dict) else None
            if not isinstance(binding, dict) or binding.get("deploy_nonce") != execution.nonce:
                continue
            if binding != expected:
                return RejectCause.APPROVAL_LOG_NONCE_REBOUND
        return None

    def delete(self, request: ApprovalRequest) -> None:
        """404-tolerant: a message the owner already removed is not a supersede failure."""
        try:
            _ = self.surface.api("DELETE", self._message_path(request))
        except HTTPError as error:
            if error.code != 404:
                raise ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise ApprovalSurfaceError(str(error)) from error

    def drop(self, request: ApprovalRequest) -> None:
        """Compare-and-swap: discard the record ONLY while it still holds this exact message."""
        if request.key != self.spec.key():
            return
        try:
            found = self.binding()
        except ApprovalRecordsError:
            return
        if found is None or (found.action_hash, found.message_id) != (
            request.action_hash,
            request.message_id,
        ):
            return
        self.path().unlink(missing_ok=True)

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        try:
            message = self.surface.api(
                "POST", f"/channels/{intent.channel_id}/messages", {"content": self.spec.render()}
            )
        except _TRANSPORT_ERRORS as error:
            raise ApprovalSurfaceError(str(error)) from error
        message_id = message.get("id") if isinstance(message, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise ApprovalSurfaceError("approval post response carries no message id")
        return PostedApproval(message_id=message_id, channel_id=intent.channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        """The ONLY writer of the pending record — it persists this run's nonce and binding."""
        del intent, created_at  # the record's field set is frozen by the gate's CLI contract
        path = self.path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        record = self.spec.serialize(self.new_record(posted))
        _ = path.write_text(record, encoding="utf-8")
        path.chmod(0o600)

    def _message_path(self, request: ApprovalRequest) -> str:
        return f"/channels/{request.channel_id}/messages/{request.message_id}"

    def _get(self, path: str) -> object | None:
        try:
            return self.surface.api("GET", path)
        except HTTPError as error:
            if error.code == 404:
                return None
            raise ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise ApprovalSurfaceError(str(error)) from error

    def _content(self, request: ApprovalRequest) -> str | None:
        message = self._get(self._message_path(request))
        content = message.get("content") if isinstance(message, dict) else None
        return content if isinstance(content, str) and content else None

    def _owner_reacted(self, request: ApprovalRequest, emoji: str) -> bool:
        users = self._get(f"{self._message_path(request)}/reactions/{quote(emoji)}?limit=100")
        if not isinstance(users, list):
            return False
        owner = self.surface.owner_id()
        return any(
            isinstance(user, dict) and str(user.get("id", "")) == owner and not bool(user.get("bot", False))
            for user in users
        )
