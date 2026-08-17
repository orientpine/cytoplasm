"""Pure scoring signals for read-only institutional-mail search."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

import triage_recipient


RecipientRole = Literal["to", "cc", "unknown"]
ContactKind = Literal["mass_notice", "direct_inquiry", "other"]

_TOKEN: Final[re.Pattern[str]] = re.compile(r"[0-9A-Za-z가-힣]+")
_GENERIC_SENDER: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[<\s])no-?reply@", re.IGNORECASE
)
_REPLY_SIGNAL: Final[re.Pattern[str]] = re.compile(
    r"\b(?:reply|question|inquiry)\b|회신|답장|문의", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class SearchDocument:
    """One already-loaded mail and its searchable projections."""

    subject: str
    sender: str
    body: str
    thread: tuple[str, ...]
    attachments: tuple[str, ...]
    markdown: str
    recipient_count: int


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Deterministic score and recipient/contact explanations."""

    score: float
    matched_fields: tuple[str, ...]
    recipient_role: RecipientRole
    contact_kind: ContactKind


def score_document(candidate: SearchDocument, query: str, *, owner: str) -> SearchResult:
    """Score query-token overlap and expose recipient/contact signals."""
    query_tokens = frozenset(token.lower() for token in _TOKEN.findall(query))
    fields = (
        ("subject", candidate.subject),
        ("sender", candidate.sender),
        ("body", candidate.body),
        ("thread", "\n".join(candidate.thread)),
        ("attachment", "\n".join(candidate.attachments)),
    )
    matched = tuple(
        name
        for name, value in fields
        if any(token in value.lower() for token in query_tokens)
    )
    role = triage_recipient.recipient_role(candidate.markdown, owner)
    mass_notice = candidate.recipient_count >= 10 and bool(
        _GENERIC_SENDER.search(candidate.sender)
    )
    direct_inquiry = (
        candidate.recipient_count <= 2
        and role == "to"
        and bool(_REPLY_SIGNAL.search(candidate.body))
    )
    if mass_notice:
        contact_kind: ContactKind = "mass_notice"
    elif direct_inquiry:
        contact_kind = "direct_inquiry"
    else:
        contact_kind = "other"
    return SearchResult(
        score=float(len(matched)),
        matched_fields=matched,
        recipient_role=role,
        contact_kind=contact_kind,
    )
