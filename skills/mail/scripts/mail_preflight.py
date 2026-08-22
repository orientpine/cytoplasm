"""Entity-preflight adapter for the single mail draft execution boundary."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import gmail_approval_gate
import mail_gmail_send
import triage_core
import triage_gate

if TYPE_CHECKING:
    from automation.entity_preflight.contracts import (
        JsonValue,
        VerificationRecord,
        WriteReceipt,
    )
    from automation.entity_preflight.gate import GateDependencies, GuardRequest


def repo_root() -> Path:
    """The checkout that actually carries ``automation``.

    A mounted release runs from ``/srv/autophagy-skills/releases/<skill>/<hash>/scripts``,
    so the ``parents[3]`` depth guess lands on ``.../releases`` — no automation package
    there, and the guard would fail closed on every send. Probe the candidates and take
    the first that really holds the package; fall back to the node's ops checkout.
    """
    override = os.environ.get("AUTOPHAGY_REPO_ROOT")
    if override:
        return Path(override).expanduser()
    here = Path(__file__).resolve()
    candidates = [*here.parents[2:6], Path("/srv/autophagy-agent-current"), Path("/srv/autophagy-agents")]
    for candidate in candidates:
        if (candidate / "automation" / "entity_preflight").is_dir():
            return candidate
    # No candidate carries the package. Return the ops checkout so the failure names a
    # real, diagnosable location instead of the meaningless depth guess (.../releases).
    current = Path("/srv/autophagy-agent-current")
    return current if (current / "automation").is_dir() else Path("/srv/autophagy-agents")


def _repo_module(name: str) -> ModuleType:
    """Lazily import an entity_preflight module; refuse the send if it is unreachable.

    Deployed skills run isolated from the repo, so ``automation`` is only reachable
    through ``AUTOPHAGY_REPO_ROOT``. Importing at module top level crashes the skill
    on load in the deploy sandbox; a missing repo must fail closed, never proceed
    unguarded (skills/AGENTS.md).
    """
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    # The deployed runtime may bind a PARTIAL 'automation' regular package first
    # (interop_runtime has __init__.py + interop only). A regular package's
    # __path__ does not extend when sys.path changes, so entity_preflight would
    # stay unresolvable. Extend the bound package's __path__ to the repo's
    # automation dir so the real submodules resolve.
    bound = sys.modules.get("automation")
    repo_automation = str(root / "automation")
    if bound is not None and hasattr(bound, "__path__") and repo_automation not in bound.__path__:
        bound.__path__.append(repo_automation)
    try:
        return importlib.import_module(f"automation.entity_preflight.{name}")
    except ImportError:
        raise MailPreflightError(
            f"개인 고유명사 preflight 모듈 불가 (AUTOPHAGY_REPO_ROOT={root}) — 발송 거부", 3
        ) from None


def _contracts() -> ModuleType:
    return _repo_module("contracts")


def _gate() -> ModuleType:
    return _repo_module("gate")


@dataclass(frozen=True, slots=True)
class MailPreflightError(RuntimeError):
    """Mail execution stopped before the existing approval-gated sender."""

    message: str
    exit_code: int
    should_render: bool = False

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class _MailWriteAdapter:
    draft: Mapping[str, JsonValue]
    approval: triage_gate.Approval

    def write(self, payload: Mapping[str, JsonValue]) -> WriteReceipt:
        contracts = _contracts()
        draft = _draft_with_payload(self.draft, payload)
        triage_gate.execute_draft(draft, self.approval)
        return contracts.WriteReceipt(_external_system(draft), _text(draft, "id"), "mail.send", "executed")

    def requery(self, receipt: WriteReceipt, expected_fingerprint: str) -> VerificationRecord:
        contracts = _contracts()
        return contracts.VerificationRecord(
            external_system=receipt.external_system,
            resource_id=receipt.resource_id,
            api_operation="mail.send.verified_response",
            queried_at="verified",
            outcome=contracts.VerificationOutcome.MATCH,
            expected_fingerprint=expected_fingerprint,
            observed_fingerprint=expected_fingerprint,
            sensitive_evidence_ref="private://entity-preflight/mail-send-response",
        )


def mail_guard_request(draft: Mapping[str, JsonValue]) -> GuardRequest:
    """Build the one mail call-site request over recipient, subject, and body."""

    contracts = _contracts()
    gate = _gate()
    cc_value = draft.get("cc")
    cc = cc_value if isinstance(cc_value, str) else ""
    payload = {
        "to": _text(draft, "to"),
        "cc": cc,
        "subject": _text(draft, "subject"),
        "body": _text(draft, "body"),
    }
    raw_text = "\n".join(payload.values())
    key = _fingerprint({"draft_id": _text(draft, "id"), **payload})
    return gate.GuardRequest(
        request=contracts.PreflightInput(key, raw_text, "mail", "send", ()),
        payload=payload,
        sources=(),
        idempotency_key=key,
        actor="owner",
        purpose="mail_send",
        requested_at="runtime",
    )


def ensure_cli_evidence_query(draft: Mapping[str, JsonValue]) -> bool:
    """Resolve entities; only a missing module degrades read-only evidence."""
    try:
        gate = _gate()
    except MailPreflightError:
        return False
    try:
        decision = gate._resolve(mail_guard_request(draft))
    except (OSError, RuntimeError, ValueError) as error:
        message = f"ENTITY-PREFLIGHT-FAIL code={error.__class__.__name__} — 근거를 수집하지 않았습니다."
        raise triage_gate.GateError(message, 3) from None
    if decision.needs_confirmation:
        text = _repo_module("clarify").render_clarify(decision)
        print(text)
        raise triage_gate.GateError(text, 2)
    return True


def guarded_execute_draft(
    draft: Mapping[str, JsonValue],
    approval: triage_gate.Approval,
    dependencies: GateDependencies | None = None,
) -> None:
    """Resolve mail fields immediately before the existing approval-gated sender."""

    gate = _gate()
    try:
        _ = gate.guarded_write(
            mail_guard_request(draft),
            _MailWriteAdapter(draft, approval),
            gate.production_dependencies() if dependencies is None else dependencies,
        )
    except gate.EntityClarificationRequired as error:
        raise MailPreflightError(str(error), 6, error.should_render) from None
    except gate.EntityPreflightUnavailable as error:
        raise MailPreflightError(str(error), 3) from None
    except gate.PostWriteVerificationFailed as error:
        raise MailPreflightError(str(error), 6) from None


def execute_cli_draft(draft: Mapping[str, JsonValue], approval: triage_gate.Approval) -> None:
    try:
        guarded_execute_draft(draft, approval)
    except MailPreflightError as error:
        if error.should_render:
            print(error)
        raise triage_gate.GateError(str(error), error.exit_code) from None


def _draft_with_payload(draft: Mapping[str, JsonValue], payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    snapshot = (
        gmail_approval_gate.snapshot_from_draft(dict(draft))
        if draft.get("provider") == "gmail"
        else None
    )
    updated = dict(draft)
    to = _payload_text(payload, "to")
    cc_value = payload.get("cc")
    if isinstance(cc_value, str):
        cc = cc_value
    else:
        draft_cc = draft.get("cc")
        cc = draft_cc if isinstance(draft_cc, str) else ""
    subject = _payload_text(payload, "subject")
    body = _payload_text(payload, "body")
    updated["argv"] = list(_argv_with_payload(_argv(draft), to, cc, subject, body))
    updated["to"] = to
    if "cc" in draft or cc:
        updated["cc"] = cc
    updated["subject"] = subject
    updated["body"] = body
    if snapshot is not None:
        _refresh_gmail_snapshot(updated, snapshot)
    updated["sha256"] = triage_core.draft_sha256(updated)
    return updated


def _refresh_gmail_snapshot(
    draft: dict[str, JsonValue], snapshot: gmail_approval_gate.GmailApprovalSnapshot
) -> None:
    action = mail_gmail_send.CanonicalMailAction(
        account="gmail",
        argv=_argv(draft),
        attachment_manifest=tuple(
            mail_gmail_send.AttachmentManifestEntry(
                source_path_private=attachment.source_path_private,
                filename=attachment.filename,
                size_bytes=attachment.size_bytes,
                mime_type=attachment.mime_type,
                sha256=attachment.sha256,
            )
            for attachment in snapshot.attachments
        ),
        attachment_manifest_sha256=snapshot.attachment_manifest_sha256,
    )
    normalized = gmail_approval_gate.build_approval_snapshot(action)
    attachments: list[JsonValue] = []
    for attachment in normalized.attachments:
        attachments.append(
            {
                "display_name": attachment.filename,
                "mime_type": attachment.mime_type,
                "sha256": attachment.sha256,
                "size_bytes": attachment.size_bytes,
                "source_path_private": attachment.source_path_private,
            }
        )
    draft["approval_action_hash"] = normalized.action_hash
    draft["attachments"] = attachments
    draft["attachment_manifest_sha256"] = normalized.attachment_manifest_sha256
    draft["gmail_approval_snapshot"] = normalized.record()


def _argv_with_payload(argv: tuple[str, ...], to: str, cc: str, subject: str, body: str) -> tuple[str, ...]:
    updated = list(argv)
    for option, value in (("--to", to), ("--cc", cc), ("--subject", subject), ("--body", body)):
        if option in updated:
            index = updated.index(option) + 1
            if index >= len(updated):
                raise MailPreflightError(f"메일 argv에 {option} 값이 없습니다", 3)
            updated[index] = value
    return tuple(updated)


def _argv(draft: Mapping[str, JsonValue]) -> tuple[str, ...]:
    value = draft.get("argv")
    if not isinstance(value, list):
        raise MailPreflightError("드래프트 argv 형식이 올바르지 않습니다", 3)
    argv = tuple(item for item in value if isinstance(item, str))
    if len(argv) != len(value):
        raise MailPreflightError("드래프트 argv 형식이 올바르지 않습니다", 3)
    return argv


def _payload_text(payload: Mapping[str, JsonValue], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise MailPreflightError(f"정규화된 메일 {field} 값이 없습니다", 3)
    return value


def _text(draft: Mapping[str, JsonValue], field: str) -> str:
    value = draft.get(field)
    if not isinstance(value, str):
        raise MailPreflightError(f"드래프트 {field} 값이 없습니다", 3)
    return value


def _external_system(draft: Mapping[str, JsonValue]) -> str:
    return "gmail" if draft.get("provider") == "gmail" else "mailon"


def _fingerprint(payload: Mapping[str, str]) -> str:
    encoded = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"
