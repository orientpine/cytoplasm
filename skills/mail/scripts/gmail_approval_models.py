"""Immutable values and typed failures for the Gmail approval gate."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeAlias

import mail_gmail_send

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class GmailApprovalGateError(RuntimeError):
    """Base error for a Gmail action that must not reach gws."""


@dataclass(frozen=True, slots=True)
class ApprovalRequiredError(GmailApprovalGateError):
    """No exactly-one unexpired owner approval matches the frozen action."""

    def __str__(self) -> str:
        return "유효한 Gmail 발송 승인이 없어 실행하지 않았습니다"


@dataclass(frozen=True, slots=True)
class ApprovalSnapshotMismatchError(GmailApprovalGateError):
    """The action or an attachment differs from the owner-approved snapshot."""

    def __str__(self) -> str:
        return "승인 후 Gmail 발송 내용 또는 첨부파일이 변경되어 재승인이 필요합니다"


@dataclass(frozen=True, slots=True)
class ApprovalAlreadyConsumedError(GmailApprovalGateError):
    """This exact approved Gmail action was already sent."""

    def __str__(self) -> str:
        return "동일한 Gmail 승인 건은 이미 발송되어 재실행하지 않았습니다"


@dataclass(frozen=True, slots=True)
class GmailAttachmentSnapshot:
    """The three owner-visible attachment facts plus the private source path."""

    source_path_private: str
    filename: str
    size_bytes: int
    mime_type: str
    sha256: str

    def record(self) -> dict[str, JsonValue]:
        """Return the approval-log representation of this frozen attachment."""
        return {
            "filename": self.filename,
            "mime_type": self.mime_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "source_path_private": self.source_path_private,
        }

    def verification_record(self) -> mail_gmail_send.TriageAttachmentManifest:
        """Adapt to the only attachment drift verifier in the mail skill."""
        return {
            "display_name": self.filename,
            "mime_type": self.mime_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "source_path_private": self.source_path_private,
        }


@dataclass(frozen=True, slots=True)
class GmailApprovalSnapshot:
    """Everything the owner approves and the execution boundary rechecks."""

    sender_account: str
    action_kind: str
    recipients: str
    subject: str
    body: str
    reply_target: str | None
    argv: tuple[str, ...]
    attachments: tuple[GmailAttachmentSnapshot, ...]
    attachment_manifest_sha256: str | None
    action_hash: str

    def record(self) -> dict[str, JsonValue]:
        """Serialize the immutable approval payload for the runtime draft/log."""
        return {
            "action_hash": self.action_hash,
            "action_kind": self.action_kind,
            "argv": list(self.argv),
            "attachments": [attachment.record() for attachment in self.attachments],
            "attachment_manifest_sha256": self.attachment_manifest_sha256,
            "body": self.body,
            "recipients": self.recipients,
            "reply_target": self.reply_target,
            "sender_account": self.sender_account,
            "subject": self.subject,
        }


@dataclass(frozen=True, slots=True)
class OwnerApproval:
    """Authenticated owner decision metadata saved with one approval snapshot."""

    owner_id: str
    message_id: str
    approved_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class GmailExecutionContext:
    """Capabilities needed by the real Gmail effect boundary."""

    approval_log: Path
    owner_id: str
    now: datetime
    runner: mail_gmail_send.CommandRunner
    environment: Mapping[str, str]
    e2e_test_mode: bool = False
