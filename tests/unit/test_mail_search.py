from __future__ import annotations

import importlib
import sys
from pathlib import Path


_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "mail" / "scripts"
sys.path.insert(0, str(_SCRIPTS))


def _mail_search():
    return importlib.import_module("mail_search")


def test_search_scores_subject_sender_body_thread_and_attachment_signals() -> None:
    mail_search = _mail_search()
    candidate = mail_search.SearchDocument(
        subject="Synthetic follow-up",
        sender="Seminar Desk <desk@example.invalid>",
        body="A retirement preparation workshop inquiry",
        thread=("Earlier continuing education question",),
        attachments=("workshop-registration.pdf",),
        markdown='---\nto: "owner@example.invalid"\ncc: ""\n---\n',
        recipient_count=1,
    )

    hit = mail_search.score_document(
        candidate,
        "seminar retirement workshop continuing registration",
        owner="owner@example.invalid",
    )

    assert hit.score > 0
    assert hit.matched_fields == ("sender", "body", "thread", "attachment")
    assert hit.recipient_role == "to"


def test_search_distinguishes_mass_notice_from_direct_inquiry() -> None:
    mail_search = _mail_search()
    notice = mail_search.SearchDocument(
        subject="Synthetic newsletter notice",
        sender="no-reply@example.invalid",
        body="Bulk distribution announcement",
        thread=(),
        attachments=(),
        markdown='---\nto: "group@example.invalid"\ncc: "owner@example.invalid"\n---\n',
        recipient_count=30,
    )
    inquiry = mail_search.SearchDocument(
        subject="Question about synthetic workshop",
        sender="Researcher <person@example.invalid>",
        body="Could you reply about my registration?",
        thread=("Re: individual registration",),
        attachments=("question.txt",),
        markdown='---\nto: "owner@example.invalid"\ncc: ""\n---\n',
        recipient_count=1,
    )

    notice_hit = mail_search.score_document(notice, "registration", owner="owner@example.invalid")
    inquiry_hit = mail_search.score_document(inquiry, "registration", owner="owner@example.invalid")

    assert notice_hit.contact_kind == "mass_notice"
    assert notice_hit.recipient_role == "cc"
    assert inquiry_hit.contact_kind == "direct_inquiry"
    assert inquiry_hit.recipient_role == "to"
