"""Canonical, approval-gated Gmail and MailOn outbound mail actions.

Building is pure apart from local attachment metadata reads. Execution is a
separate operation that requires the caller to supply its approval callback.
The approval gate remains the authority that verifies action and attachment
hashes immediately before it allows the callback to return.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, TypeAlias, TypedDict, assert_never

import triage_core
from mail_account_routing import Account, select_account
from triage_gate import mailon_python

GWS_BINARY: Final = "gws"
GWS_SEND_TIMEOUT_SECONDS: Final = 900


class TriageAttachmentManifest(TypedDict):
    """Attachment record shape consumed by the existing pre-send verifier."""

    source_path_private: str
    display_name: str
    size_bytes: int
    mime_type: str
    sha256: str


@dataclass(frozen=True, slots=True)
class AttachmentManifestEntry:
    """Approval-bound metadata for one local file, without its contents."""

    source_path_private: str
    filename: str
    size_bytes: int
    mime_type: str
    sha256: str

    def for_verification(self) -> TriageAttachmentManifest:
        """Return the exact legacy shape for ``verify_attachment_manifest``."""
        return {
            "source_path_private": self.source_path_private,
            "display_name": self.filename,
            "size_bytes": self.size_bytes,
            "mime_type": self.mime_type,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class DeliveryOptions:
    """Delivery settings common to new mail and thread replies."""

    account: str | None
    attachments: tuple[str | Path, ...] = ()
    cc: str | None = None
    bcc: str | None = None
    from_address: str | None = None


@dataclass(frozen=True, slots=True)
class NewMailRequest:
    """A canonical new-mail request before account routing."""

    options: DeliveryOptions
    to: str
    subject: str
    body: str


@dataclass(frozen=True, slots=True)
class ReplyMailRequest:
    """A canonical reply request before account routing."""

    options: DeliveryOptions
    reply_to_account: str | None
    reply_message_id: str | None
    body: str
    to: str = ""
    subject: str = ""


MailActionRequest: TypeAlias = NewMailRequest | ReplyMailRequest


@dataclass(frozen=True, slots=True)
class CanonicalMailAction:
    """Frozen command and attachment evidence for one owner-approved effect."""

    account: Account
    argv: tuple[str, ...]
    attachment_manifest: tuple[AttachmentManifestEntry, ...]
    attachment_manifest_sha256: str | None

    def manifest_for_verification(self) -> list[TriageAttachmentManifest]:
        """Render the exact attachment sequence that the existing gate re-reads."""
        return [entry.for_verification() for entry in self.attachment_manifest]


@dataclass(frozen=True, slots=True)
class MissingReplyMessageIdError(ValueError):
    """A reply action lacks the Gmail message id required for thread binding."""

    def __str__(self) -> str:
        return "답장에는 reply_message_id가 필요합니다"


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """The bounded gws process result returned after the gate authorizes it."""

    returncode: int
    stdout: str
    stderr: str


class CompletedProcessLike(Protocol):
    """The subprocess result surface this module needs."""

    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    """A testable, argv-only subprocess boundary."""

    def __call__(
        self,
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
        env: Mapping[str, str],
    ) -> CompletedProcessLike: ...


ApprovedExecution: TypeAlias = Callable[[CanonicalMailAction], None]


def build_action(request: MailActionRequest) -> CanonicalMailAction:
    """Build a frozen action without invoking gws or any other subprocess."""
    match request:
        case NewMailRequest():
            return _build_new_mail_action(request)
        case ReplyMailRequest():
            return _build_reply_mail_action(request)
        case unreachable:
            assert_never(unreachable)


def execute_approved(
    action: CanonicalMailAction,
    *,
    approved_execution: ApprovedExecution,
    runner: CommandRunner = subprocess.run,
    environment: Mapping[str, str] | None = None,
) -> ExecutionResult:
    """Run a frozen argv only after the caller's approval callback returns."""
    approved_execution(action)
    env = dict(os.environ) if environment is None else dict(environment)
    process = runner(
        list(action.argv),
        capture_output=True,
        text=True,
        timeout=GWS_SEND_TIMEOUT_SECONDS,
        check=False,
        env=env,
    )
    return ExecutionResult(process.returncode, process.stdout, process.stderr)


def _build_new_mail_action(request: NewMailRequest) -> CanonicalMailAction:
    account = select_account(request.options.account)
    attachments = _attachment_manifest(request.options.attachments)
    match account:
        case "gmail":
            argv = _gmail_send_argv(request, attachments)
        case "kimm":
            argv = triage_core.build_send_argv(
                mailon_python(),
                request.to,
                request.subject,
                request.body,
                _attachment_paths(attachments),
            )
        case unreachable:
            assert_never(unreachable)
    return _canonical_action(account, argv, attachments)


def _build_reply_mail_action(request: ReplyMailRequest) -> CanonicalMailAction:
    reply_message_id = _reply_message_id(request.reply_message_id)
    account = select_account(request.options.account, reply_to_account=request.reply_to_account)
    attachments = _attachment_manifest(request.options.attachments)
    match account:
        case "gmail":
            argv = _gmail_reply_argv(request, reply_message_id, attachments)
        case "kimm":
            argv = triage_core.build_send_argv(
                mailon_python(),
                request.to,
                request.subject,
                request.body,
                _attachment_paths(attachments),
            )
        case unreachable:
            assert_never(unreachable)
    return _canonical_action(account, argv, attachments)


def _gmail_send_argv(
    request: NewMailRequest,
    attachments: tuple[AttachmentManifestEntry, ...],
) -> tuple[str, ...]:
    argv = [
        GWS_BINARY,
        "gmail",
        "+send",
        "--to",
        request.to,
        "--subject",
        request.subject,
        "--body",
        request.body,
    ]
    _append_gmail_delivery_options(argv, request.options, attachments)
    return tuple(argv)


def _gmail_reply_argv(
    request: ReplyMailRequest,
    reply_message_id: str,
    attachments: tuple[AttachmentManifestEntry, ...],
) -> tuple[str, ...]:
    argv = [
        GWS_BINARY,
        "gmail",
        "+reply",
        "--message-id",
        reply_message_id,
        "--body",
        request.body,
    ]
    if request.to:
        argv.extend(("--to", request.to))
    _append_gmail_delivery_options(argv, request.options, attachments)
    return tuple(argv)


def _append_gmail_delivery_options(
    argv: list[str],
    options: DeliveryOptions,
    attachments: tuple[AttachmentManifestEntry, ...],
) -> None:
    if options.cc is not None:
        argv.extend(("--cc", options.cc))
    if options.bcc is not None:
        argv.extend(("--bcc", options.bcc))
    if options.from_address is not None:
        argv.extend(("--from", options.from_address))
    for attachment in attachments:
        argv.extend(("-a", attachment.source_path_private))


def _attachment_manifest(
    paths: tuple[str | Path, ...],
) -> tuple[AttachmentManifestEntry, ...]:
    records = triage_core.build_attachment_manifest(paths)
    return tuple(
        AttachmentManifestEntry(
            source_path_private=str(record["source_path_private"]),
            filename=str(record["display_name"]),
            size_bytes=int(record["size_bytes"]),
            mime_type=str(record["mime_type"]),
            sha256=str(record["sha256"]),
        )
        for record in records
    )


def _attachment_paths(
    attachments: tuple[AttachmentManifestEntry, ...],
) -> tuple[str, ...]:
    return tuple(entry.source_path_private for entry in attachments)


def _canonical_action(
    account: Account,
    argv: tuple[str, ...],
    attachments: tuple[AttachmentManifestEntry, ...],
) -> CanonicalMailAction:
    manifest = [entry.for_verification() for entry in attachments]
    digest = triage_core.attachment_manifest_sha256(manifest) if manifest else None
    return CanonicalMailAction(account, argv, attachments, digest)


def _reply_message_id(raw: str | None) -> str:
    if raw is None or not raw.strip():
        raise MissingReplyMessageIdError()
    return raw.strip()
