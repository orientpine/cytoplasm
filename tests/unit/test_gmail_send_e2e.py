"""Offline Gmail send-to-approval-gate verification for repair t_55928756."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))

import gmail_approval_gate as gmail_gate  # noqa: E402
import mail_gmail_send  # noqa: E402
import triage_cli  # noqa: E402
import triage_confirm  # noqa: E402
import triage_core  # noqa: E402
import triage_gate  # noqa: E402
from automation.interop import injection_adapter  # noqa: E402
from test_gmail_approval_gate import (  # noqa: E402
    NOW,
    OWNER_ID,
    _approved_record,
    _context,
    _recording_runner,
    _write_records,
)

_DM_CHANNEL = "100000000000000002"
_MESSAGE_ID = "gmail-e2e-message"


def _new_action(
    *, body: str = "Approved body.", attachments: tuple[Path, ...] = ()
) -> mail_gmail_send.CanonicalMailAction:
    return mail_gmail_send.build_action(
        mail_gmail_send.NewMailRequest(
            options=mail_gmail_send.DeliveryOptions(account="gmail", attachments=attachments),
            to="recipient@example.test",
            subject="Gmail E2E subject",
            body=body,
        )
    )


def _approve(log: Path, snapshot: gmail_gate.GmailApprovalSnapshot) -> None:
    _write_records(log, _approved_record(snapshot))


def _stored_gmail_draft(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    action: mail_gmail_send.CanonicalMailAction,
) -> tuple[gmail_gate.GmailApprovalSnapshot, dict, Path]:
    approval_log = tmp_path / "approvals.jsonl"
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    monkeypatch.setenv("TRIAGE_APPROVAL_LOG", str(approval_log))
    snapshot = gmail_gate.build_approval_snapshot(action)
    draft = triage_gate.create_gmail_draft(snapshot)
    bound = triage_gate.set_approval_binding(
        draft,
        kind="compose",
        surface="owner-dm",
        channel_id=_DM_CHANNEL,
        policy_version=1,
    )
    return snapshot, triage_gate.set_message_id(bound, _MESSAGE_ID, _DM_CHANNEL), approval_log


def _install_fake_gws(
    monkeypatch: pytest.MonkeyPatch, calls: list[tuple[str, ...]], approval_log: Path
) -> None:
    environment = {"PATH": "/offline", "TRIAGE_APPROVAL_LOG": str(approval_log)}
    if os.environ.get("E2E_TEST_MODE") == "1":
        environment["E2E_TEST_MODE"] = "1"
    monkeypatch.setattr(
        mail_gmail_send.subprocess,
        "run",
        _recording_runner(calls, expected_environment=environment),
    )
    monkeypatch.setattr(triage_gate, "os", SimpleNamespace(environ=environment))


def test_gmail_new_mail_when_approved_then_executes_frozen_argv_once(tmp_path: Path) -> None:
    # Given: a Gmail new-mail action and one matching approval record
    action = _new_action()
    snapshot = gmail_gate.build_approval_snapshot(action)
    log, calls = tmp_path / "approvals.jsonl", []
    _approve(log, snapshot)

    # When: the final gate executes it
    result = gmail_gate.execute_approved_gmail(snapshot, action, _context(log, calls))

    # Then: the recorded exact argv and action hash authorize exactly one effect
    record = next(json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if json.loads(line)["action"] == "external_effect.approval")
    assert result == mail_gmail_send.ExecutionResult(0, "submitted", "")
    assert calls == [snapshot.argv]
    assert tuple(record["argv"]) == calls[0]
    assert record["hash"] == snapshot.action_hash


def test_gmail_reply_when_approved_then_preserves_message_threading(tmp_path: Path) -> None:
    # Given: a Gmail reply inheriting its source account and message id
    action = mail_gmail_send.build_action(
        mail_gmail_send.ReplyMailRequest(
            options=mail_gmail_send.DeliveryOptions(account=None),
            reply_to_account="gmail",
            reply_message_id="thread-message-42",
            body="Reply body.",
        )
    )
    snapshot = gmail_gate.build_approval_snapshot(action)
    log, calls = tmp_path / "approvals.jsonl", []
    _approve(log, snapshot)

    # When: the final gate sends the reply
    gmail_gate.execute_approved_gmail(snapshot, action, _context(log, calls))

    # Then: gws receives the frozen reply command with the original thread target
    assert calls == [
        ("gws", "gmail", "+reply", "--message-id", "thread-message-42", "--body", "Reply body.")
    ]


def test_gmail_multiple_attachments_when_approved_then_preserves_order_and_metadata(
    tmp_path: Path,
) -> None:
    # Given: two ordered files with different contents
    first, second = tmp_path / "first.txt", tmp_path / "second.csv"
    first.write_bytes(b"one\n")
    second.write_bytes(b"two,two\n")
    action = _new_action(attachments=(first, second))
    snapshot = gmail_gate.build_approval_snapshot(action)
    draft = gmail_gate.approval_draft(snapshot, draft_id="gmail-e2e", created_at="2026-07-29T00:00:00Z")
    display = triage_core.render_approvals_message(
        draft, destination=triage_core.ApprovalRenderDestination.OWNER_DM
    )
    log, calls = tmp_path / "approvals.jsonl", []
    _approve(log, snapshot)

    # When: the final gate recomputes and executes the approved attachments
    reread = gmail_gate.recompute_attachment_manifest(snapshot)
    gmail_gate.execute_approved_gmail(snapshot, action, _context(log, calls))

    # Then: DM, snapshot, reread facts and argv retain each attachment's order
    assert calls == [snapshot.argv]
    assert [entry.filename for entry in snapshot.attachments] == ["first.txt", "second.csv"]
    expected = [(item.filename, item.size_bytes, item.sha256) for item in snapshot.attachments]
    stored = draft["gmail_approval_snapshot"]["attachments"]
    assert [(item["filename"], item["size_bytes"], item["sha256"]) for item in stored] == expected == [
        (item.filename, item.size_bytes, item.sha256) for item in reread
    ]
    for item in snapshot.attachments:
        assert item.filename in display
        assert str(item.size_bytes) in display
        assert item.sha256 in display


def test_kimm_when_selected_then_retains_unchanged_mailon_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an explicit KIMM request
    monkeypatch.setattr(mail_gmail_send, "mailon_python", lambda: "mailon-python")
    request = mail_gmail_send.NewMailRequest(
        options=mail_gmail_send.DeliveryOptions(account="kimm"),
        to="recipient@example.test",
        subject="KIMM regression",
        body="Legacy body.",
    )

    # When: the shared sender routing builds an action
    action = mail_gmail_send.build_action(request)

    # Then: the established MailOn argv is byte-for-byte unchanged
    assert action.argv == triage_core.build_send_argv(
        "mailon-python", "recipient@example.test", "KIMM regression", "Legacy body."
    )


def test_gmail_reaction_approval_when_draft_is_bound_then_executes_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a persisted Gmail draft and a bound owner ✅ decision
    attachment = tmp_path / "reaction.txt"
    attachment.write_bytes(b"reaction\n")
    action = _new_action(attachments=(attachment,))
    snapshot, draft, log = _stored_gmail_draft(monkeypatch, tmp_path, action)
    calls: list[tuple[str, ...]] = []
    _install_fake_gws(monkeypatch, calls, log)
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_confirm, "resolve_reaction", lambda _draft: triage_confirm.APPROVE_EMOJI)

    # When: the existing draft execution path consumes the reaction approval
    triage_gate.execute_draft(
        draft, triage_gate.Approval(f"reaction:{_MESSAGE_ID}", "manual_reaction", OWNER_ID)
    )

    # Then: the approved log row and gws invocation have the identical bound action
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    approval = next(record for record in records if record["action"] == "external_effect.approval")
    assert calls == [snapshot.argv]
    assert tuple(approval["argv"]) == calls[0]
    assert approval["hash"] == snapshot.action_hash


def test_gmail_permitted_text_approval_when_signed_in_e2e_then_executes_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a valid signed owner text approval for a persisted Gmail draft
    action = _new_action()
    snapshot, draft, log = _stored_gmail_draft(monkeypatch, tmp_path, action)
    injection = tmp_path / "approval.json"
    calls: list[tuple[str, ...]] = []
    monkeypatch.setenv("E2E_TEST_MODE", "1")
    monkeypatch.setenv("INTEROP_E2E_SECRET", "e2e-secret")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_confirm, "_adapter", lambda: injection_adapter)
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda _content: "notified")
    triage_confirm.sign_injection(draft, injection, None, None, False)
    _install_fake_gws(monkeypatch, calls, log)

    # When: the existing confirm entry point validates and consumes the signed text
    assert triage_cli.cmd_confirm(argparse.Namespace(draft=draft["id"], injection_file=str(injection))) == 0

    # Then: the E2E-only text method authorizes precisely the frozen gws argv once
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    approval = next(record for record in records if record["action"] == "external_effect.approval")
    assert calls == [snapshot.argv]
    assert approval["approval"]["method"] == "signed_injection_e2e"
    assert approval["hash"] == snapshot.action_hash


def test_gmail_approval_when_rejected_then_does_not_invoke_gws(tmp_path: Path) -> None:
    # Given: a recorded rejection for a frozen Gmail action
    action = _new_action()
    snapshot = gmail_gate.build_approval_snapshot(action)
    record = _approved_record(snapshot)
    record["result"] = {"status": "rejected"}
    log, calls = tmp_path / "approvals.jsonl", []
    _write_records(log, record)

    # When: execution requests authority
    with pytest.raises(gmail_gate.ApprovalRequiredError):
        gmail_gate.execute_approved_gmail(snapshot, action, _context(log, calls))

    # Then: a rejected decision produces no external effect
    assert calls == []


def test_gmail_approval_when_expired_then_does_not_invoke_gws(tmp_path: Path) -> None:
    # Given: an approval record whose expiry is before the execution clock
    action = _new_action()
    snapshot = gmail_gate.build_approval_snapshot(action)
    record = _approved_record(snapshot)
    record["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
    log, calls = tmp_path / "approvals.jsonl", []
    _write_records(log, record)

    # When: execution reaches the approval boundary
    with pytest.raises(gmail_gate.ApprovalRequiredError):
        gmail_gate.execute_approved_gmail(snapshot, action, _context(log, calls))

    # Then: expired authority cannot start gws
    assert calls == []


def test_gmail_send_before_approval_then_does_not_invoke_gws(tmp_path: Path) -> None:
    # Given: a frozen Gmail action without an approval record
    action = _new_action()
    snapshot = gmail_gate.build_approval_snapshot(action)
    calls: list[tuple[str, ...]] = []

    # When: execution is attempted before approval
    with pytest.raises(gmail_gate.ApprovalRequiredError):
        gmail_gate.execute_approved_gmail(snapshot, action, _context(tmp_path / "approvals.jsonl", calls))

    # Then: gws has never been called
    assert calls == []


def test_gmail_body_changed_after_approval_then_does_not_invoke_gws(tmp_path: Path) -> None:
    # Given: approval for one exact Gmail body
    approved = _new_action(body="Approved body.")
    snapshot = gmail_gate.build_approval_snapshot(approved)
    log, calls = tmp_path / "approvals.jsonl", []
    _approve(log, snapshot)

    # When: the body is rebuilt after approval
    with pytest.raises(gmail_gate.ApprovalSnapshotMismatchError):
        gmail_gate.execute_approved_gmail(snapshot, _new_action(body="Changed body."), _context(log, calls))

    # Then: the bound approval cannot release a changed message
    assert calls == []


def test_gmail_attachment_swapped_content_changed_or_deleted_then_does_not_invoke_gws(
    tmp_path: Path,
) -> None:
    # Given: three independent approved attachment fixtures
    for mutation in ("swap", "change", "delete"):
        attachment = tmp_path / f"{mutation}.txt"
        attachment.write_bytes(b"approved\n")
        action = _new_action(attachments=(attachment,))
        snapshot = gmail_gate.build_approval_snapshot(action)
        log, calls = tmp_path / f"{mutation}.jsonl", []
        _approve(log, snapshot)

        # When: the file is replaced, content-changed, or deleted before execution
        match mutation:
            case "swap":
                attachment.unlink()
                attachment.write_bytes(b"replacement\n")
            case "change":
                attachment.write_bytes(b"changed\n")
            case "delete":
                attachment.unlink()
            case unexpected:
                raise AssertionError(f"unexpected mutation {unexpected}")
        with pytest.raises(gmail_gate.ApprovalSnapshotMismatchError):
            gmail_gate.execute_approved_gmail(snapshot, action, _context(log, calls))

        # Then: no drift variant reaches the fake gws boundary
        assert calls == []


def test_gmail_duplicate_approval_and_retry_then_never_double_sends(tmp_path: Path) -> None:
    # Given: duplicate approvals for one action, then one retryable approval
    action = _new_action()
    snapshot = gmail_gate.build_approval_snapshot(action)
    record = _approved_record(snapshot)
    log, calls = tmp_path / "approvals.jsonl", []
    _write_records(log, record, _approved_record(snapshot))

    # When: duplicate authority is rejected, then a single approval succeeds and is retried
    with pytest.raises(gmail_gate.ApprovalRequiredError):
        gmail_gate.execute_approved_gmail(snapshot, action, _context(log, calls))
    _write_records(log, record)
    gmail_gate.execute_approved_gmail(snapshot, action, _context(log, calls))
    with pytest.raises(gmail_gate.ApprovalAlreadyConsumedError):
        gmail_gate.execute_approved_gmail(snapshot, action, _context(log, calls))

    # Then: neither duplicate authority nor a post-success retry can double-send
    assert calls == [snapshot.argv]
