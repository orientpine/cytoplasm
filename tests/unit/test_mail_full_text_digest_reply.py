"""Q474: full text uses native replies under its mail card, without a new thread."""
from __future__ import annotations

from tests.unit import test_mail_digest_reactions as digest
from tests.unit.test_mail_digest_reactions import db as db


def test_full_text_parts_are_replies_to_the_original_digest_card(db, monkeypatch):
    digest._discord(monkeypatch, {})
    monkeypatch.setattr(digest.triage_transport, "_get_mail", digest._mail)
    posted = []
    monkeypatch.setattr(
        digest.triage_confirm, "post_in",
        lambda channel, body, reply_to="": posted.append((channel, reply_to)) or "reply",
    )
    assert digest.actions._show_body({
        "uid": "u-1", "subject": "sample", "channel_id": "thread-1", "message_id": "card-1",
    }) == "done"
    assert posted == [("thread-1", "card-1"), ("thread-1", "card-1")]
    assert digest.OPENED == []
