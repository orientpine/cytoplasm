"""Pure Discord projection of the body already disclosed by the mail wrapper."""
from __future__ import annotations

import html
import re
from typing import Final, TypedDict

import mail_quote

LIMIT: Final = 1900
_QUOTE: Final = re.compile(r"^-{2,}\s*(?:원본 메시지|Original Message|Forwarded message)\s*-{2,}$", re.I)
_FOOTER: Final = re.compile(r"^(?:This (?:email|e-mail|message) is intended\b|본 메일은|Sent from my\b)", re.I)
_BREAK: Final = re.compile(r"</?(?:br|div|p)\b[^>]*>", re.I)
_STYLE: Final = re.compile(r"</?(?:span|font)\b[^>]*>", re.I)
_LINK: Final = re.compile(r"!?\[[^\]\n]*\]\([^\s)]*(?:\([^)]*\)[^\s)]*)*\)|https?://[^\s]+")
_ATTACHMENTS: Final = re.compile(r"\A---\n.*?^attachments:[ \t]*\n((?:[ \t]+-[^\n]*\n)*)", re.M | re.S)


class Mail(TypedDict, total=False):
    """Only fields the existing read-only wrapper has already disclosed."""

    subject: str
    sender: str
    date: str
    body: str | None
    body_note: str


def render(mail: Mail, *, full: bool = False) -> list[str]:
    """Format headers/body; retain quoted history only when explicitly requested.

    No files, services, or sensitivity rules are consulted here. Masking belongs
    to the wrapper, before this projection. Quote counts include separator lines
    and blank lines in a trailing original, before whitespace normalization.
    """
    original = mail_quote.parse_original(mail)
    raw = (mail.get("body") or "").replace("\r\n", "\n")
    attachments = _ATTACHMENTS.search(raw)
    count = len(attachments[1].splitlines()) if attachments else 0
    recipients = original.to or "(없음)"
    if original.cc:
        recipients += f" · 참조: {original.cc}"
    header = "\n".join((
        f"- 제목: {' '.join(original.subject.split()) or '(제목 없음)'}",
        f"- 보낸 사람: {' '.join(original.sender.split()) or '(없음)'}",
        f"- 받는 사람·참조: {' '.join(recipients.split())}",
        f"- 날짜: {' '.join(original.date.split()) or '(없음)'}",
        f"- 첨부 {count}건",
    ))
    body = html.unescape(_STYLE.sub("", _BREAK.sub("\n", original.body)))
    lines: list[str] = []
    quoted = 0
    original_tail = False
    footer = False
    signature = False
    for line in body.splitlines():
        stripped = line.strip()
        original_tail = original_tail or bool(_QUOTE.fullmatch(stripped))
        if original_tail or stripped.startswith(">"):
            if full:
                lines.append(line)
            else:
                quoted += 1
            continue
        if footer:
            continue
        if signature:
            if stripped:
                lines.append(stripped + " …")
                footer = True
            continue
        if stripped == "--":
            signature = True
            continue
        if _FOOTER.match(stripped):
            lines.append(stripped + " …")
            footer = True
            continue
        lines.append(line if stripped else "")
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if not text:
        text = mail.get("body_note") or "(빈 본문)"
    if quoted:
        text += f"\n\n— 원문 인용 {quoted}줄 생략 (전체: `--full`) —"
    return _chunks(header + "\n\n" + text)


def _chunks(text: str) -> list[str]:
    """Prefer paragraph boundaries, then lines/words; never lose source text.

    A paragraph larger than a message must be split. Links fitting the budget
    stay intact. Only an indivisible over-budget token gets a hard character cut.
    Separators belong to the next chunk so concatenation recovers the projection.
    """
    if len(text) <= LIMIT:
        return [text]
    # The character count bounds the chunk count, including suffix digit growth.
    budget = LIMIT - (5 + 2 * len(str(len(text))))
    chunks: list[str] = []
    start = 0
    links = iter(_LINK.finditer(text))
    link = next(links, None)
    while start < len(text):
        end = min(start + budget, len(text))
        if end < len(text):
            for separator in ("\n\n", "\n", " "):
                boundary = text.rfind(separator, start + 2, end)
                if boundary > start and text[start:boundary].strip():
                    end = boundary
                    break
            while link is not None and link.end() <= start:
                link = next(links, None)
            while link is not None and link.end() <= end:
                link = next(links, None)
            if link is not None and link.start() < end < link.end():
                if start < link.start():
                    end = link.start()
                elif link.end() - start <= budget:
                    end = link.end()
        chunks.append(text[start:end])
        start = end
    total = len(chunks)
    return [f"{chunk}\n\n({index}/{total})" for index, chunk in enumerate(chunks, 1)]
