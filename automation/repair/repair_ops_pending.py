"""Durable, redacted pending-state and posting adapter for repair approvals."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TypeAlias

from automation.interop.approval_lifecycle import ApprovalRecordsError
from automation.interop.approval_surface import ApprovalKind, ApprovalSurface
from automation.repair.repair_approval_render import approval_request_content
from automation.repair.repair_ops_approval import repair_action_hash
from automation.repair.repair_patch_binding import (
    ContentBinding,
    PatchBindingError,
    PatchFileDelta,
    V2_KEYS as _V2_KEYS,
    changes_to_json,
    content_action_hash,
    decode_content_binding,
)


APPROVE_EMOJI = "✅"
CANCEL_EMOJI = "⛔"

__all__ = (
    "APPROVE_EMOJI",
    "CANCEL_EMOJI",
    "ApprovalRequestTransport",
    "PendingApprovalError",
    "PendingRepairApproval",
    "PendingRepairApprovalStore",
    "PostingOwnerApproval",
    "approval_request_content",
)
JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class JsonLoader(Protocol):
    """Narrow json.loads at the trust boundary before pending-state parsing."""

    def __call__(self, s: str) -> JsonValue: ...


JSON_LOADS: JsonLoader = json.loads


class PendingApprovalError(RuntimeError):
    """A pending repair approval could not be safely persisted or decoded."""


class ApprovalRequestTransport(Protocol):
    """Minimal Discord posting surface used only after sandbox success."""

    def post_approval(self, content: str) -> str: ...

    def add_reaction(self, message_id: str, emoji: str) -> None: ...


@dataclass(frozen=True, slots=True)
class PendingRepairApproval:
    """Safe state necessary to bind one future owner reaction to one repair."""

    ticket_id: str
    patch_name: str
    action_hash: str
    nonce: str
    message_id: str
    created_at: datetime
    content_binding_version: int | None = None
    patch_sha256: str | None = None
    changes: tuple[PatchFileDelta, ...] | None = None
    patch_source_path: str | None = None
    # The resolved approval binding stays LAST: a shared conformance check reads
    # the final four annotated fields of every pending record in the system.
    kind: ApprovalKind | None = None
    surface: ApprovalSurface | None = None
    channel_id: str | None = None
    policy_version: int | None = None


@dataclass(frozen=True, slots=True)
class PendingRepairApprovalStore:
    """Atomically persist repair approval state under the ops-private root."""

    root: Path

    def get(self, ticket_id: str) -> PendingRepairApproval | None:
        """Return one pending request, or None when the ticket has no request."""
        path = self._path(ticket_id)
        if not path.is_file():
            return None
        return self._decode(path.read_text(encoding="utf-8"))

    def all(self) -> tuple[PendingRepairApproval, ...]:
        """Return all active requests without exposing raw patch contents."""
        if not self.root.is_dir():
            return ()
        return tuple(self._decode(path.read_text(encoding="utf-8")) for path in sorted(self.root.glob("*.json")))

    def all_strict(self) -> tuple[PendingRepairApproval, ...]:
        """List for the approval adapter: a record we cannot read is never "absent"."""
        if not self.root.is_dir():
            return ()
        found: list[PendingRepairApproval] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                found.append(self._decode(path.read_text(encoding="utf-8")))
            except (OSError, PendingApprovalError) as error:
                raise ApprovalRecordsError(str(path)) from error
        return tuple(found)

    def save(self, pending: PendingRepairApproval) -> None:
        """Atomically record a newly posted request after its reactions are ready."""
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _ = self.root.chmod(0o700)
        target = self._path(pending.ticket_id)
        temporary = target.with_suffix(".tmp")
        payload: dict[str, JsonValue] = {
            "ticket_id": pending.ticket_id,
            "patch_name": pending.patch_name,
            "action_hash": pending.action_hash,
            "nonce": pending.nonce,
            "message_id": pending.message_id,
            "created_at": pending.created_at.astimezone(UTC).isoformat(),
            "kind": None if pending.kind is None else pending.kind.value,
            "surface": None if pending.surface is None else pending.surface.value,
            "channel_id": pending.channel_id,
            "policy_version": pending.policy_version,
        }
        if pending.content_binding_version is not None:
            if pending.patch_sha256 is None or pending.changes is None or pending.patch_source_path is None:
                raise PendingApprovalError("content-bound repair approval is incomplete")
            payload |= {
                "content_binding_version": pending.content_binding_version,
                "patch_sha256": pending.patch_sha256,
                "changes": list(changes_to_json(pending.changes)),
                "patch_source_path": pending.patch_source_path,
            }
        _ = temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        _ = temporary.chmod(0o600)
        os.replace(temporary, target)

    def remove(self, ticket_id: str) -> None:
        """Discard completed or terminal approval state only after its command succeeds."""
        path = self._path(ticket_id)
        if path.exists():
            path.unlink()

    def drop(self, pending: PendingRepairApproval) -> None:
        """Compare-and-swap removal: re-read and discard only an unchanged binding."""
        path = self._path(pending.ticket_id)
        if not path.is_file():
            return
        try:
            current = self._decode(path.read_text(encoding="utf-8"))
        except (OSError, PendingApprovalError):
            return
        binding = (current.ticket_id, current.action_hash, current.message_id)
        if binding == (pending.ticket_id, pending.action_hash, pending.message_id):
            path.unlink(missing_ok=True)

    def claim(self, ticket_id: str) -> bool:
        """Claim one pending entry so overlapping watcher ticks cannot apply twice."""
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _ = self.root.chmod(0o700)
        lock = self._lock_path(ticket_id)
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
        os.close(descriptor)
        return True

    def release(self, ticket_id: str) -> None:
        """Release an unsuccessful claim so the next watcher tick may retry."""
        lock = self._lock_path(ticket_id)
        if lock.exists():
            lock.unlink()

    def _path(self, ticket_id: str) -> Path:
        return self.root / f"{hashlib.sha256(ticket_id.encode()).hexdigest()}.json"

    def _lock_path(self, ticket_id: str) -> Path:
        return self.root / f"{hashlib.sha256(ticket_id.encode()).hexdigest()}.lock"

    @staticmethod
    def _decode(raw: str) -> PendingRepairApproval:
        try:
            decoded = JSON_LOADS(raw)
        except json.JSONDecodeError as error:
            raise PendingApprovalError("pending repair approval JSON is invalid") from error
        if not isinstance(decoded, dict):
            raise PendingApprovalError("pending repair approval fields are invalid")
        binding = _decode_content_binding(decoded)
        fields: dict[str, str] = {}
        for key, value in decoded.items():
            if key in _V2_KEYS:
                continue
            match value:
                case str():
                    fields[key] = value
                case None if key in {"kind", "surface", "channel_id", "policy_version"}:
                    continue
                case int() if key == "policy_version" and not isinstance(value, bool):
                    continue
                case _:
                    raise PendingApprovalError("pending repair approval fields are invalid")
        required = ("ticket_id", "patch_name", "action_hash", "nonce", "message_id", "created_at")
        if any(key not in fields for key in required):
            raise PendingApprovalError("pending repair approval is incomplete")
        try:
            created_at = datetime.fromisoformat(fields["created_at"])
        except ValueError as error:
            raise PendingApprovalError("pending repair approval timestamp is invalid") from error
        if created_at.tzinfo is None:
            raise PendingApprovalError("pending repair approval timestamp lacks timezone")
        kind, surface, channel_id, policy_version = _decode_binding(decoded)
        pending = PendingRepairApproval(
            fields["ticket_id"],
            fields["patch_name"],
            fields["action_hash"],
            fields["nonce"],
            fields["message_id"],
            created_at,
            *binding,
            kind,
            surface,
            channel_id,
            policy_version,
        )
        if pending.action_hash != _expected_action_hash(pending):
            raise PendingApprovalError("pending repair approval hash is invalid")
        return pending




def _expected_action_hash(pending: PendingRepairApproval) -> str:
    """Re-derive the binding a record claims, from the record's own fields.

    For a v2 record this catches a partial write or a hand-edited summary; the
    binding that actually authorises an apply is checked against the patch on
    disk in ``ManualOwnerApproval``, never here.
    """
    if pending.content_binding_version is None or pending.patch_sha256 is None or pending.changes is None:
        return repair_action_hash(pending.ticket_id, pending.patch_name)
    return content_action_hash(
        pending.ticket_id, pending.patch_name, pending.patch_sha256, pending.changes
    )


def _decode_content_binding(decoded: dict[str, JsonValue]) -> ContentBinding:
    try:
        return decode_content_binding(decoded)
    except PatchBindingError as error:
        raise PendingApprovalError(str(error)) from error


def _decode_binding(
    decoded: dict[str, JsonValue],
) -> tuple[ApprovalKind | None, ApprovalSurface | None, str | None, int | None]:
    raw_kind = decoded.get("kind")
    raw_surface = decoded.get("surface")
    raw_channel_id = decoded.get("channel_id")
    raw_policy_version = decoded.get("policy_version")
    match raw_kind, raw_surface, raw_channel_id, raw_policy_version:
        case None, None, None, None:
            return None, None, None, None
        case str() as kind, str() as surface, str() as channel_id, int() as policy_version if not isinstance(policy_version, bool):
            try:
                return ApprovalKind(kind), ApprovalSurface(surface), channel_id, policy_version
            except ValueError as error:
                raise PendingApprovalError("pending repair approval binding is invalid") from error
        case _:
            raise PendingApprovalError("pending repair approval binding is incomplete")


from automation.repair.repair_ops_posting import PostingOwnerApproval  # noqa: E402, F401
