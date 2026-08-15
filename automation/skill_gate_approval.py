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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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

    def valid_approval(self, execution: ApprovalExecution, approval_log: Path) -> bool:
        """Accept only the current owner decision for one complete execution binding."""
        return self._execution_state(execution, approval_log) is Probe.APPROVED

    def valid_binding(self, execution: ApprovalExecution, approval_log: Path) -> bool:
        """Accept a current undecided or approved binding for signed E2E owner injection."""
        return self._execution_state(execution, approval_log) in (
            Probe.APPROVED,
            Probe.BOUND_PENDING,
        )

    def _execution_state(
        self, execution: ApprovalExecution, approval_log: Path
    ) -> Probe | None:
        request = execution.request
        try:
            record = self.stored()
            if record is None:
                return None
            found = self.spec.stored(record)
            channel_id = self._channel_of(record)
        except (ApprovalRecordsError, ApprovalSurfaceError):
            return None
        if found is None or (
            request.key,
            found.action_hash,
            found.message_id,
            found.nonce,
            record.get("approval_action", ""),
            record.get("approval_destination", ""),
            channel_id,
        ) != (
            self.spec.key(),
            request.action_hash,
            request.message_id,
            execution.nonce,
            execution.action,
            execution.destination,
            request.channel_id,
        ):
            return None
        if self._pending_nonce_reused(execution.nonce) or self._approval_nonce_reused(
            execution, approval_log
        ):
            return None
        try:
            probe_result = self.probe(request)
            return probe_result
        except ApprovalSurfaceError:
            return None

    def _pending_nonce_reused(self, nonce: str) -> bool:
        for candidate in self.path().parent.glob("*.json"):
            if candidate == self.path():
                continue
            try:
                decoded = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return True
            if not isinstance(decoded, dict):
                return True
            candidate_nonce = decoded.get("deploy_nonce", decoded.get("publish_nonce"))
            if candidate_nonce == nonce:
                return True
        return False

    def _approval_nonce_reused(self, execution: ApprovalExecution, approval_log: Path) -> bool:
        try:
            lines = approval_log.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return False
        except OSError:
            return True
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
                return True
        return False

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
