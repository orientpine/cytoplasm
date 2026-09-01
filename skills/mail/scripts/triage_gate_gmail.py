"""Gmail(gws) 실행 백엔드 — 승인 이후의 발송만 담당한다 (W4-2, 제약 1/6).

``triage_gate`` 에서 분리(G8 LOC). MailOn 백엔드와 달리 이 경로는 소유자 반응을
``triage_confirm`` 으로 한 번 더 해석한 뒤 Gmail 전용 최종 게이트
(``gmail_approval_gate``)에 넘긴다 — 승인 소유자·참조·방법이 전부 일치할 때만
얼어붙은 argv 가 실행된다.

``gmail_approval_gate``·``mail_gmail_send``·``triage_confirm`` 은 모두 파사드를
역import 하므로 함수 안에서 늦게 import 한다(분리 전과 동일). 환경 변수는
``triage_gate.os`` 를 거쳐 읽는다 — 그 모듈 속성이 테스트가 고정하는 이음매다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import triage_gate
from triage_mode import append_record as _append_record

GMAIL_APPROVAL_TTL = timedelta(minutes=15)


def execute_gmail_draft(draft: dict, approval: triage_gate.Approval) -> None:
    """Reuse the mail reaction resolver before the Gmail-only final gate."""
    import gmail_approval_gate
    import mail_gmail_send
    import triage_confirm

    if approval.owner != triage_confirm.owner_id():
        raise triage_gate.GateError("Gmail 발송 승인 소유자가 일치하지 않음 — 실행 중단", 1)
    match approval.method:
        case "manual_reaction":
            if approval.ref != f"reaction:{draft['message_id']}":
                raise triage_gate.GateError("Gmail 발송 승인 참조 불일치 — 실행 중단", 1)
            if triage_confirm.resolve_reaction(draft) != triage_confirm.APPROVE_EMOJI:
                raise triage_gate.GateError("Gmail 소유자 승인이 유효하지 않음 — 실행 중단", 1)
            approval_method: gmail_approval_gate.GmailApprovalMethod = "manual_reaction"
        case "signed_injection_e2e":
            if triage_gate.os.environ.get("E2E_TEST_MODE") != "1":
                raise triage_gate.GateError("Gmail 텍스트 승인은 E2E_TEST_MODE에서만 허용", 1)
            approval_method = "signed_injection_e2e"
        case _:
            raise triage_gate.GateError("Gmail 발송 승인 방법이 허용되지 않음 — 실행 중단", 1)
    snapshot = gmail_approval_gate.snapshot_from_draft(draft)
    now = datetime.now(UTC)
    _append_record(
        triage_gate._approval_log(),
        gmail_approval_gate.approval_record(
            snapshot,
            gmail_approval_gate.OwnerApproval(
                owner_id=approval.owner,
                message_id=draft["message_id"],
                approved_at=now,
                expires_at=now + GMAIL_APPROVAL_TTL,
            ),
            method=approval_method,
        ),
    )
    action = mail_gmail_send.CanonicalMailAction(
        account="gmail",
        argv=snapshot.argv,
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
    result = gmail_approval_gate.execute_approved_gmail(
        snapshot,
        action,
        gmail_approval_gate.GmailExecutionContext(
            approval_log=triage_gate._approval_log(),
            owner_id=approval.owner,
            now=now,
            runner=mail_gmail_send.subprocess.run,
            environment=dict(triage_gate.os.environ),
            e2e_test_mode=triage_gate.os.environ.get("E2E_TEST_MODE") == "1",
        ),
    )
    if result.returncode != 0:
        raise triage_gate.GateError("Gmail 발송에 실패했습니다. 재승인이 필요합니다", 6)
    path = triage_gate._draft_path(draft["id"])
    if path is not None:
        triage_gate.write_json(path, {**draft, "status": "executed", "approval_ref": approval.ref, "method": approval.method})
