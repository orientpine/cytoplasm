"""Unpostable mail must never reserve the journal key (t_82644d12)."""
from __future__ import annotations

import pytest

from tests.unit import test_mail_single_live_request as mail
from tests.unit.test_mail_single_live_request import mail_env as mail_env


def test_an_oversize_summary_is_refused_before_the_journal_reserves(mail_env):
    # Given a subject that cannot fit even in the attachment summary.
    fake, _, _ = mail_env
    draft = {**mail._draft(), "subject": "x" * 2001}
    # When / Then refusal precedes every external effect and journal reservation.
    with pytest.raises(mail.triage_gate.GateError) as raised:
        mail.triage_approval.request_approval(draft)
    assert raised.value.exit_code == 3
    assert mail.triage_approval.posting_journal().outstanding(mail.triage_approval.approval_key(draft)) is None
    assert fake.posts == 0
    assert fake.notices == {}
    assert fake.threads == {}


def test_a_normal_approval_still_reaches_the_lifecycle(mail_env):
    fake, _, _ = mail_env
    draft = mail._draft()
    verdict = mail.triage_approval.request_approval(draft)
    assert verdict.outcome is mail.triage_approval.lifecycle().Outcome.POSTED
    assert fake.posts == 1
    stored = mail.triage_gate.load_draft(draft["id"])
    assert fake.contents[stored["message_id"]] == mail.triage_approval._approval_content(stored, "")
