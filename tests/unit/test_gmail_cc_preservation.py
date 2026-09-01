"""Gmail 승인 초안의 Cc 보존 회귀 (repair t_0c46c0ad).

승인 → ``mail_preflight`` → 실제 gws 발송까지 Cc 가 살아남는지, 그리고 승인 메시지가
고정한 draft sha256 · action hash 가 preflight 뒤에도 그대로인지 고정한다. 판정은
출하물(저장된 초안 레코드 · 실행된 argv · 승인 로그 레코드)로만 한다.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import gmail_approval_gate as gmail_gate  # noqa: E402
import mail_gmail_send  # noqa: E402
import mail_preflight  # noqa: E402
import triage_confirm  # noqa: E402
import triage_core  # noqa: E402
import triage_gate  # noqa: E402
from automation.entity_preflight.contracts import JsonValue  # noqa: E402
from automation.entity_preflight.gate import (  # noqa: E402
    GateDependencies,
    InMemoryIdempotencyStore,
)

OWNER_ID = "owner-cc"
DM_CHANNEL = "100000000000000002"
MESSAGE_ID = "gmail-cc-approval"
TO = "recipient@example.com"
CC = "partner@example.com"


class _AuditSink:
    def append(self, event: Mapping[str, JsonValue]) -> str:
        del event
        return "private://test/gmail-cc"


def _dependencies() -> GateDependencies:
    return GateDependencies(_AuditSink(), None, InMemoryIdempotencyStore())


def _gmail_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Confine every gate path to tmp_path; never ~/.hermes, never a real mailbox."""
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    monkeypatch.setenv("TRIAGE_APPROVAL_LOG", str(tmp_path / "approvals.jsonl"))
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    return tmp_path / "approvals.jsonl"


def _cc_action() -> mail_gmail_send.CanonicalMailAction:
    return mail_gmail_send.build_action(
        mail_gmail_send.NewMailRequest(
            options=mail_gmail_send.DeliveryOptions(account="gmail", cc=CC),
            to=TO,
            subject="Cc preservation regression",
            body="Please review the attached plan.",
        )
    )


def _posted_draft(snapshot: gmail_gate.GmailApprovalSnapshot) -> dict:
    """A Gmail draft persisted, bound to the owner DM, and posted for approval."""
    draft = triage_gate.create_gmail_draft(snapshot)
    bound = triage_gate.set_approval_binding(
        draft, kind="compose", surface="owner-dm", channel_id=DM_CHANNEL, policy_version=1
    )
    return triage_gate.set_message_id(bound, MESSAGE_ID, DM_CHANNEL)


def _discord_api(content: str):
    """The owner-DM approval message plus one owner ✅ on it."""
    approve = quote(triage_confirm.APPROVE_EMOJI, safe="")

    def request(method: str, path: str, payload: dict | None = None):
        del payload
        if method == "GET" and path == f"/channels/{DM_CHANNEL}/messages/{MESSAGE_ID}":
            return {"id": MESSAGE_ID, "content": content}
        if method == "GET" and path.endswith(f"/reactions/{approve}?limit=100"):
            return [{"id": OWNER_ID, "bot": False}]
        if method == "GET" and "/reactions/" in path:
            return []
        raise AssertionError(f"unexpected Discord call: {method} {path}")

    return request


def _recording_runner(calls: list[tuple[str, ...]]):
    def run(args: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(tuple(args))
        return SimpleNamespace(returncode=0, stdout="submitted", stderr="")

    return run


def _argv_options(argv: tuple[str, ...]) -> list[tuple[str, str]]:
    return [(argv[index], argv[index + 1]) for index in range(len(argv) - 1)]


def _stored_draft(tmp_path: Path, draft_id: str) -> dict:
    path = tmp_path / "gate" / "drafts" / f"{draft_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _records(approval_log: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in approval_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_create_gmail_draft_when_the_action_carries_a_cc_then_the_draft_ships_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a canonical Gmail action addressed to one recipient with one Cc
    _gmail_environment(monkeypatch, tmp_path)
    snapshot = gmail_gate.build_approval_snapshot(_cc_action())

    # When: the approval draft is persisted for the owner gate
    draft = triage_gate.create_gmail_draft(snapshot)

    # Then: the stored draft carries the Cc its frozen argv and snapshot already had
    stored = _stored_draft(tmp_path, str(draft["id"]))
    assert stored["cc"] == CC
    assert ("--cc", CC) in _argv_options(tuple(stored["argv"]))
    assert stored["approval_action_hash"] == snapshot.action_hash
    assert stored["sha256"] == triage_core.draft_sha256(stored)


def test_gmail_cc_draft_when_owner_approves_then_preflight_sends_the_approved_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: an owner ✅ on the approval message of a Gmail draft carrying a Cc
    approval_log = _gmail_environment(monkeypatch, tmp_path)
    snapshot = gmail_gate.build_approval_snapshot(_cc_action())
    draft = _posted_draft(snapshot)
    posted = triage_core.render_approvals_message(
        draft, destination=triage_core.ApprovalRenderDestination.OWNER_DM
    )
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_confirm, "_api", _discord_api(posted))
    monkeypatch.setattr(mail_gmail_send.subprocess, "run", _recording_runner(calls))

    # When: the approved draft crosses mail_preflight into the real send boundary
    mail_preflight.guarded_execute_draft(
        draft,
        triage_gate.Approval(f"reaction:{MESSAGE_ID}", "manual_reaction", OWNER_ID),
        _dependencies(),
    )

    # Then: the executed argv, the approval record and the stored draft all keep the Cc
    stored = _stored_draft(tmp_path, str(draft["id"]))
    approval = next(
        record
        for record in _records(approval_log)
        if record["action"] == "external_effect.approval"
    )
    assert calls == [snapshot.argv]
    assert ("--cc", CC) in _argv_options(calls[0])
    assert stored["cc"] == CC
    assert stored["status"] == "executed"
    assert approval["hash"] == snapshot.action_hash
    assert draft["sha256"] in posted and snapshot.action_hash in posted


def test_mail_preflight_when_a_stored_draft_has_no_cc_field_then_the_argv_cc_survives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a Gmail draft persisted before the record carried a Cc field
    _gmail_environment(monkeypatch, tmp_path)
    snapshot = gmail_gate.build_approval_snapshot(_cc_action())
    posted = _posted_draft(snapshot)
    legacy = {key: value for key, value in posted.items() if key != "cc"}
    legacy["sha256"] = triage_core.draft_sha256(legacy)
    executed: list[Mapping[str, JsonValue]] = []
    monkeypatch.setattr(
        triage_gate, "execute_draft", lambda current, _approval: executed.append(current)
    )

    # When: the entity preflight normalizes that draft immediately before the send
    mail_preflight.guarded_execute_draft(
        legacy,
        triage_gate.Approval(f"reaction:{MESSAGE_ID}", "manual_reaction", OWNER_ID),
        _dependencies(),
    )

    # Then: the Cc frozen into the approved argv survives and no hash moves under it
    assert len(executed) == 1
    normalized = executed[0]
    assert ("--cc", CC) in _argv_options(tuple(normalized["argv"]))
    assert normalized["sha256"] == legacy["sha256"]
    assert normalized["approval_action_hash"] == snapshot.action_hash
    assert gmail_gate.snapshot_from_draft(dict(normalized)).argv == snapshot.argv


def test_owner_dm_when_the_gmail_draft_carries_a_cc_then_the_owner_sees_it() -> None:
    # Given: a Gmail approval draft whose frozen argv and record both carry a Cc
    snapshot = gmail_gate.build_approval_snapshot(_cc_action())
    draft = gmail_gate.approval_draft(
        snapshot, draft_id="gmail-cc-dm", created_at="2026-08-27T00:00:00Z"
    )

    # When: the owner DM that the approval gate posts is rendered from that draft
    owner_dm = triage_core.render_approvals_message(
        draft, destination=triage_core.ApprovalRenderDestination.OWNER_DM
    )

    # Then: the copied recipient is visible on the very message the owner approves,
    # in the same shape the compose branch already uses.
    assert f"- Cc: `{CC}`" in owner_dm
    assert draft["approval_action_hash"] in owner_dm


def test_owner_dm_when_the_gmail_draft_has_no_cc_then_it_shows_no_cc_line() -> None:
    # Given: the same mail addressed to one recipient with nobody in copy
    snapshot = gmail_gate.build_approval_snapshot(
        mail_gmail_send.build_action(
            mail_gmail_send.NewMailRequest(
                options=mail_gmail_send.DeliveryOptions(account="gmail"),
                to=TO,
                subject="Cc preservation regression",
                body="Please review the attached plan.",
            )
        )
    )
    draft = gmail_gate.approval_draft(
        snapshot, draft_id="gmail-no-cc-dm", created_at="2026-08-27T00:00:00Z"
    )

    # When: the owner DM is rendered
    owner_dm = triage_core.render_approvals_message(
        draft, destination=triage_core.ApprovalRenderDestination.OWNER_DM
    )

    # Then: no Cc line at all — an empty one would read as a recipient withheld
    assert "- Cc:" not in owner_dm
    assert f"- 수신자: `{TO}`" in owner_dm
