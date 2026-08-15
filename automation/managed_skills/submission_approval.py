"""Shared-lifecycle adapter for member-submitted personal skill artifacts."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, TypeAlias, final

from automation.interop.approval_lease import FileKeyLease, PostingJournal
from automation.interop.approval_lifecycle import (
    ApprovalIntent,
    ApprovalRecordsError,
    ApprovalRequest,
    ApprovalSurfaceError,
    PostedApproval,
    Probe,
    Verdict,
    request_owner_approval,
)
from automation.managed_skills.submission_artifact import (
    SubmissionArtifact,
    validate_submission_artifact,
)
from automation.managed_skills.submission_errors import SubmissionArtifactError
from automation.managed_skills.submission_message import (
    SubmissionEnvelope,
    SubmissionIdentity,
    new_submission_envelope,
    parse_submission_message,
    render_submission_message,
)
from automation.managed_skills.submission_transport import (
    SubmissionAttachment,
    SubmissionTransport,
    SubmissionTransportError,
)
from automation.skill_gate_specs import APPROVE_EMOJI, CANCEL_EMOJI, binding_fields
from automation.skill_gate_surface import SupplyChainSurface

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class _JsonLoader(Protocol):
    def __call__(self, raw: str, /) -> JsonValue: ...


_JSON_LOADS: _JsonLoader = json.loads

_REQUIRED_RECORD_FIELDS: Final = frozenset(
    {
        "action_hash",
        "channel_id",
        "content",
        "created_at",
        "kind",
        "message_id",
        "policy_version",
        "reviewer_id",
        "surface",
    }
)


@dataclass(frozen=True, slots=True)
class SubmissionApprovalConfig:
    artifact: SubmissionArtifact
    group_id: str
    submitter: str
    reviewer_id: str
    surface: SupplyChainSurface
    transport: SubmissionTransport
    state_root: Path


@dataclass(frozen=True, slots=True)
class _Record:
    action_hash: str
    channel_id: str
    content: str
    created_at: str
    kind: str
    message_id: str
    policy_version: str
    reviewer_id: str
    surface: str

    def mapping(self) -> dict[str, str]:
        return {
            "action_hash": self.action_hash,
            "channel_id": self.channel_id,
            "content": self.content,
            "created_at": self.created_at,
            "kind": self.kind,
            "message_id": self.message_id,
            "policy_version": self.policy_version,
            "reviewer_id": self.reviewer_id,
            "surface": self.surface,
        }


def _parse_record(path: Path) -> _Record | None:
    try:
        raw = _JSON_LOADS(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as error:
        raise ApprovalRecordsError(str(path)) from error
    if not isinstance(raw, dict) or frozenset(raw) != _REQUIRED_RECORD_FIELDS:
        raise ApprovalRecordsError(str(path))
    if any(not isinstance(value, str) for value in raw.values()):
        raise ApprovalRecordsError(str(path))
    return _Record(
        action_hash=str(raw["action_hash"]),
        channel_id=str(raw["channel_id"]),
        content=str(raw["content"]),
        created_at=str(raw["created_at"]),
        kind=str(raw["kind"]),
        message_id=str(raw["message_id"]),
        policy_version=str(raw["policy_version"]),
        reviewer_id=str(raw["reviewer_id"]),
        surface=str(raw["surface"]),
    )


@final
class PersonalSubmissionGate:
    """ApprovalGate implementation that posts two immutable Discord attachments."""

    def __init__(
        self,
        config: SubmissionApprovalConfig,
        envelope: SubmissionEnvelope,
    ) -> None:
        self.config: Final = config
        self.envelope: Final = envelope
        self.binding: Final = config.surface.new()

    def path(self) -> Path:
        digest = self.envelope.action_hash.removeprefix("sha256:")
        return self.config.state_root / "pending" / f"{digest}.json"

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        if key != self.key():
            raise ApprovalRecordsError(key)
        record = _parse_record(self.path())
        if record is None:
            return ()
        return (
            ApprovalRequest(
                key=key,
                action_hash=record.action_hash,
                message_id=record.message_id,
                channel_id=record.channel_id,
                created_at=record.created_at,
            ),
        )

    def probe(self, request: ApprovalRequest) -> Probe:
        record = _parse_record(self.path())
        if record is None:
            return Probe.BINDING_MISMATCH
        if (request.action_hash, request.message_id, request.channel_id) != (
            record.action_hash,
            record.message_id,
            record.channel_id,
        ):
            return Probe.BINDING_MISMATCH
        try:
            _ = self.config.surface.stored(record.mapping())
            message = self.config.transport.fetch_message(request.channel_id, request.message_id)
            if message is None:
                return Probe.MISSING
            envelope = parse_submission_message(message.content)
            if message.content != record.content or envelope.action_hash != record.action_hash:
                return Probe.BINDING_MISMATCH
            if message.attachment_names != envelope.attachment_names:
                return Probe.BINDING_MISMATCH
            cancelled = self._reviewer_reacted(record, CANCEL_EMOJI)
            approved = self._reviewer_reacted(record, APPROVE_EMOJI)
        except (SubmissionArtifactError, SubmissionTransportError, OSError) as error:
            raise ApprovalSurfaceError(str(error)) from error
        if cancelled:
            return Probe.CANCELLED
        if approved:
            return Probe.APPROVED
        return Probe.BOUND_PENDING

    def _reviewer_reacted(self, record: _Record, emoji: str) -> bool:
        users = self.config.transport.reaction_users(record.channel_id, record.message_id, emoji)
        return any(user.user_id == record.reviewer_id and not user.bot for user in users)

    def delete(self, request: ApprovalRequest) -> None:
        try:
            self.config.transport.delete_message(request.channel_id, request.message_id)
        except SubmissionTransportError as error:
            raise ApprovalSurfaceError(str(error)) from error

    def drop(self, request: ApprovalRequest) -> None:
        record = _parse_record(self.path())
        if record is None:
            return
        if (record.action_hash, record.message_id) == (request.action_hash, request.message_id):
            self.path().unlink(missing_ok=True)

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        current = validate_submission_artifact(
            self.config.artifact.tarball_path,
            self.config.artifact.manifest_path,
            expected_tarball_sha256=self.config.artifact.tarball_sha256,
        )
        if current.manifest_sha256 != self.config.artifact.manifest_sha256:
            raise SubmissionArtifactError("submission manifest sha256 differs from its pinned value")
        attachments = (
            SubmissionAttachment(self.envelope.tarball_filename, current.tarball_path),
            SubmissionAttachment(self.envelope.manifest_filename, current.manifest_path),
        )
        try:
            message_id = self.config.transport.post_submission(
                intent.channel_id,
                render_submission_message(self.envelope),
                attachments,
            )
            self.config.transport.add_reaction(intent.channel_id, message_id, APPROVE_EMOJI)
            self.config.transport.add_reaction(intent.channel_id, message_id, CANCEL_EMOJI)
        except SubmissionTransportError as error:
            raise ApprovalSurfaceError(str(error)) from error
        return PostedApproval(message_id, intent.channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        if intent.action_hash != self.envelope.action_hash:
            raise ApprovalRecordsError("submission action hash changed before commit")
        values = {
            "action_hash": intent.action_hash,
            "content": render_submission_message(self.envelope),
            "created_at": created_at,
            "message_id": posted.message_id,
            "reviewer_id": self.config.reviewer_id,
            **binding_fields(self.binding),
        }
        path = self.path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                _ = handle.write(json.dumps(values, separators=(",", ":"), sort_keys=True))
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as error:
            raise ApprovalRecordsError(str(path)) from error

    def key(self) -> str:
        return f"skill-submit:{self.envelope.action_hash}"


def request_submission_approval(config: SubmissionApprovalConfig) -> Verdict:
    """Post or reuse one immutable review request through approval_lifecycle."""
    artifact = validate_submission_artifact(
        config.artifact.tarball_path,
        config.artifact.manifest_path,
        expected_tarball_sha256=config.artifact.tarball_sha256,
    )
    if artifact.manifest_sha256 != config.artifact.manifest_sha256:
        raise SubmissionArtifactError("submission manifest sha256 differs from its pinned value")
    envelope = new_submission_envelope(
        SubmissionIdentity(config.group_id, config.submitter),
        artifact,
        secrets.token_hex(16),
    )
    gate = PersonalSubmissionGate(config, envelope)
    intent = ApprovalIntent(gate.key(), envelope.action_hash, gate.binding.channel_id)
    return request_owner_approval(
        intent,
        gate,
        FileKeyLease(config.state_root / "approval-leases"),
        PostingJournal(config.state_root / "posting-journal"),
    )
