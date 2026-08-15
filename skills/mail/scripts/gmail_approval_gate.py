"""Fail-closed approval binding immediately before a Gmail subprocess effect."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal, TypeAlias

import mail_gmail_send
import triage_core
import triage_mode
from gmail_approval_models import (
    ApprovalAlreadyConsumedError,
    ApprovalRequiredError,
    ApprovalSnapshotMismatchError,
    GmailApprovalSnapshot,
    GmailAttachmentSnapshot,
    GmailExecutionContext,
    JsonValue,
    OwnerApproval,
)
from gmail_approval_snapshot import (
    GMAIL_TARGET_ID,
    approval_draft as _approval_draft,
    build_approval_snapshot,
    snapshot_from_draft as _snapshot_from_draft,
)

GmailApprovalMethod: TypeAlias = Literal["manual_reaction", "signed_injection_e2e"]


def approval_draft(
    snapshot: GmailApprovalSnapshot, *, draft_id: str, created_at: str
) -> dict[str, JsonValue]:
    """Render the exact snapshot-bound Gmail owner-DM approval draft."""
    return _approval_draft(snapshot, draft_id=draft_id, created_at=created_at)


def snapshot_from_draft(draft: dict[str, JsonValue]) -> GmailApprovalSnapshot:
    """Parse the stored Gmail draft before the final approval execution boundary."""
    return _snapshot_from_draft(draft)


def approval_record(
    snapshot: GmailApprovalSnapshot,
    approval: OwnerApproval,
    *,
    method: GmailApprovalMethod = "manual_reaction",
) -> dict[str, JsonValue]:
    """Return the approval-log record the pre-execution gate requires exactly."""
    return {
        "action": "external_effect.approval",
        "approval": {
            "channel": "approvals",
            "message_id": approval.message_id,
            "method": method,
            "owner_id": approval.owner_id,
        },
        "approval_snapshot": snapshot.record(),
        "argv": list(snapshot.argv),
        "expires_at": approval.expires_at.isoformat(),
        "hash": snapshot.action_hash,
        "result": {"status": "approved"},
        "target_id": GMAIL_TARGET_ID,
        "timestamp": approval.approved_at.isoformat(),
    }


def recompute_attachment_manifest(
    snapshot: GmailApprovalSnapshot,
) -> tuple[GmailAttachmentSnapshot, ...]:
    """Re-read attachment metadata for the triple-equality regression assertion."""
    records = triage_core.build_attachment_manifest(
        tuple(attachment.source_path_private for attachment in snapshot.attachments)
    )
    return tuple(
        GmailAttachmentSnapshot(
            source_path_private=str(record["source_path_private"]),
            filename=str(record["display_name"]),
            size_bytes=int(record["size_bytes"]),
            mime_type=str(record["mime_type"]),
            sha256=str(record["sha256"]),
        )
        for record in records
    )


def execute_approved_gmail(
    snapshot: GmailApprovalSnapshot,
    action: mail_gmail_send.CanonicalMailAction,
    context: GmailExecutionContext,
) -> mail_gmail_send.ExecutionResult:
    """Require one matching approval, then re-verify immediately before gws."""
    records = _read_records(context.approval_log)
    matching = tuple(
        record
        for record in records
        if _is_current_approval(
            record, snapshot, context.owner_id, context.now, context.e2e_test_mode
        )
    )
    if len(matching) != 1:
        raise ApprovalRequiredError()
    if any(_is_execution(record, snapshot) for record in records):
        raise ApprovalAlreadyConsumedError()
    if build_approval_snapshot(action) != snapshot:
        raise ApprovalSnapshotMismatchError()
    if snapshot.attachments:
        try:
            triage_core.verify_attachment_manifest(
                [attachment.verification_record() for attachment in snapshot.attachments],
                snapshot.attachment_manifest_sha256 or "",
            )
        except triage_core.AttachmentPolicyError as error:
            raise ApprovalSnapshotMismatchError() from error
    process = context.runner(
        list(snapshot.argv),
        capture_output=True,
        text=True,
        timeout=mail_gmail_send.GWS_SEND_TIMEOUT_SECONDS,
        check=False,
        env=context.environment,
    )
    result = mail_gmail_send.ExecutionResult(process.returncode, process.stdout, process.stderr)
    if result.returncode == 0:
        triage_mode.append_record(context.approval_log, _execution_record(snapshot))
    return result


def _read_records(path: Path) -> tuple[dict[str, JsonValue], ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return ()
    records: list[dict[str, JsonValue]] = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return tuple(records)


def _is_current_approval(
    record: dict[str, JsonValue],
    snapshot: GmailApprovalSnapshot,
    owner_id: str,
    now: datetime,
    e2e_test_mode: bool,
) -> bool:
    approval = record.get("approval")
    if not isinstance(approval, dict):
        return False
    expires_at = _parse_datetime(record.get("expires_at"))
    manual_allowed = approval.get("method") == "manual_reaction"
    e2e_allowed = e2e_test_mode and approval.get("method") == "signed_injection_e2e"
    return (
        expires_at is not None
        and expires_at > now
        and record.get("action") == "external_effect.approval"
        and record.get("approval_snapshot") == snapshot.record()
        and record.get("argv") == list(snapshot.argv)
        and record.get("hash") == snapshot.action_hash
        and record.get("result") == {"status": "approved"}
        and record.get("target_id") == GMAIL_TARGET_ID
        and approval.get("channel") == "approvals"
        and (manual_allowed or e2e_allowed)
        and approval.get("owner_id") == owner_id
    )


def _parse_datetime(value: JsonValue | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _is_execution(record: dict[str, JsonValue], snapshot: GmailApprovalSnapshot) -> bool:
    return (
        record.get("action") == "gmail.approval_execution"
        and record.get("argv") == list(snapshot.argv)
        and record.get("hash") == snapshot.action_hash
        and record.get("result") == {"status": "submitted"}
        and record.get("target_id") == GMAIL_TARGET_ID
    )


def _execution_record(snapshot: GmailApprovalSnapshot) -> dict[str, JsonValue]:
    return {
        "action": "gmail.approval_execution",
        "argv": list(snapshot.argv),
        "hash": snapshot.action_hash,
        "result": {"status": "submitted"},
        "target_id": GMAIL_TARGET_ID,
    }
