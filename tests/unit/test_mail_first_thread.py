"""Q474: terminal and superseded cards do not give one mail another thread."""
from __future__ import annotations

import pytest

from tests.unit import test_mail_single_live_request as mail
from tests.unit.test_mail_single_live_request import mail_env as mail_env

import triage_binding


@pytest.mark.parametrize("status", ["expired", "cancelled", "superseded"])
def test_redraft_reuses_first_mail_thread_after_terminal_card(mail_env, monkeypatch, status):
    fake, _, directory = mail_env
    first = mail._draft(body="first sample")
    mail.triage_approval.post_for_approval(first)
    stored = mail._stored(directory, first["id"])
    thread = stored["approval_thread_id"]
    mail.triage_gate.write_json(directory / f"{first['id']}.json", {
        **stored, "status": status, "expired_reason": "superseded-by-newer-draft",
    })
    # A later legacy record must not take precedence over the first thread.
    later = {**stored, "id": "later", "created": "2099-01-01T00:00:00Z",
             "approval_thread_id": "123", "status": "expired"}
    monkeypatch.setattr(mail.triage_gate, "list_drafts", lambda: [later, {
        **stored, "status": status,
    }])
    redraft = mail._draft(body="replacement sample")
    binding = triage_binding.stored_binding(redraft)
    assert binding.channel_id == thread
    assert len(fake.threads) == 1
    assert mail.triage_approval.approval_key(first) == mail.triage_approval.approval_key(redraft)


def test_another_mail_never_reuses_a_thread_by_subject(mail_env):
    fake, _, _ = mail_env
    mail.triage_approval.post_for_approval(mail._draft(uid="first-mail"))
    binding = triage_binding.stored_binding(mail._draft(uid="different-mail"))
    assert binding.channel_id in fake.threads
    assert len(fake.threads) == 2
