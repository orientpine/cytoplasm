"""Heal mails saved before the 2026-10-19 attachment fixes, from inside the regular sync.

Two repairs, both idempotent and cheap once done:

* ``prune_buttons`` - webmail buttons recorded as attachments ("Save", "Download all") are
  dropped from state.db and from the mail's Markdown lists. Their files go too when every real
  attachment of that mail is saved (otherwise the "Download all" zip may be the only copy).
* ``backfill_bcc`` - a sent mail saved before the writer kept Bcc (no ``bcc:`` front-matter line)
  gets the Bcc its webmail view lists, written into the front matter only. Owner Q393 2026-10-30:
  YooDori sees recipients when it opens one mail. The line itself marks the mail done.
* ``backfill_large_links`` - a mail of the last ``BACKFILL_DAYS`` whose body shows MailOn's
  "대용량 첨부파일" box but has no large-file link recorded is read again, so the links the old
  parser missed are downloaded and listed. Each mail is looked at once (``large_link_checked``).
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

from .scraper import BUTTON_NAMES, InboxScraper, MailRef, is_large_file_link, parse_view_async_html
from .state import StateDB
from .writer import _yaml_escape, write_mail


log = logging.getLogger(__name__)

LARGE_BOX_MARK = "대용량 첨부파일"
BACKFILL_DAYS = 30
# Re-reads per sync run; the sync wrapper stops a run after 15 minutes.
BACKFILL_MAX_PER_RUN = 3
# Bcc re-reads are one view_async fetch each (no attachment download); ~25 per 10-minute tick.
BCC_BACKFILL_MAX_PER_RUN = 25


def drop_listed_names(md: str, names: Iterable[str]) -> str:
    """Remove `names` from a mail Markdown's front-matter list and its ``## Attachments`` section."""
    names = frozenset(names)
    lines = md.split("\n")
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return md
    end = lines.index("---", 1)
    items = {f"  - {_yaml_escape(n)}" for n in names}
    front = [line for line in lines[1:end] if line not in items]
    for i, line in enumerate(front):
        if line == "attachments:" and not (i + 1 < len(front) and front[i + 1].startswith("  - ")):
            front[i] = "attachments:[]"

    links = tuple(f"- [{n}](" for n in names)
    body: list[str] = []
    section: list[str] | None = None
    for line in lines[end + 1:] + ["## "]:  # sentinel closes a trailing section
        if line.startswith("## "):
            if section is not None and any(s.startswith("- [") for s in section):
                body.extend(section)
            section = [line] if line == "## Attachments" else None
            if section is None:
                body.append(line)
            continue
        if section is not None:
            if not line.startswith(links):
                section.append(line)
            continue
        body.append(line)
    body.pop()  # the sentinel
    return "\n".join(["---", *front, "---", *body])


def prune_buttons(db: StateDB, project_root: Path, attachments_dir: Path) -> int:
    """Drop button rows, Markdown entries and (when safe) files. Returns the number of mails healed."""
    healed = 0
    for uid in db.uids_with_attachment_names(BUTTON_NAMES):
        rows = db.attachment_rows(uid)
        real = [r for r in rows if r["filename"] not in BUTTON_NAMES]
        msg = db.message_row(uid)
        md = project_root / msg["markdown_path"] if msg and msg.get("markdown_path") else None
        if md is not None and md.exists():
            text = md.read_text(encoding="utf-8")
            new = drop_listed_names(text, BUTTON_NAMES)
            if new != text:
                md.write_text(new, encoding="utf-8")
        if real and all(r["status"] == "ok" for r in real):
            for r in rows:
                if r["filename"] in BUTTON_NAMES:
                    (attachments_dir / uid / InboxScraper._sanitize_filename(r["filename"])).unlink(
                        missing_ok=True)
        db.delete_attachments(uid, BUTTON_NAMES)
        healed += 1
    if healed:
        log.info("removed webmail buttons listed as attachments from %d mail(s)", healed)
    return healed


def _needs_backfill(db: StateDB, project_root: Path, msg: dict) -> bool:
    md = project_root / (msg.get("markdown_path") or "")
    if not msg.get("markdown_path") or not md.exists():
        return False
    if LARGE_BOX_MARK not in md.read_text(encoding="utf-8"):
        return False
    return not any(is_large_file_link(r["href"]) for r in db.attachment_rows(msg["uid"]))


def _folder_uid(folder: str, scraper: InboxScraper, folder_uids: dict[str, str]) -> str | None:
    if folder == "inbox":
        return scraper.folder_uid
    if folder.startswith("folder:"):
        return folder.removeprefix("folder:")
    return folder_uids.get(folder)


def backfill_large_links(
    db: StateDB, scraper: InboxScraper, mails_dir: Path, project_root: Path,
    *, folder_uids: dict[str, str] | None = None, now: datetime | None = None,
) -> int:
    """Re-read recent mails whose large-file links were missed. Returns the number re-read."""
    since = ((now or datetime.now()) - timedelta(days=BACKFILL_DAYS)).isoformat(timespec="seconds")
    tried = reread = 0
    for msg in db.unchecked_messages_since(since):
        if tried >= BACKFILL_MAX_PER_RUN:
            break
        uid = msg["uid"]
        if not _needs_backfill(db, project_root, msg):
            db.mark_large_link_checked(uid)
            continue
        folder_uid = _folder_uid(msg["folder"], scraper, folder_uids or {})
        if folder_uid is None:  # e.g. the sent folder was not resolved this run; try again next run
            continue
        tried += 1
        ref = MailRef(
            uid=uid, folder_uid=folder_uid, subject=msg["subject"] or "", sender=msg["sender"] or "",
            to="", date=datetime.fromisoformat(msg["recv_date"]), size=0, unread=False,
            flagged=False, attach_count=0,
        )
        try:
            mail = scraper.read_mail(ref, state_db=db)
        except Exception as e:  # the next run tries again while the mail is inside the window
            log.warning("large-link backfill: re-read failed uid=%s: %s", uid, str(e)[:200])
            continue
        mail.folder = msg["folder"]
        old = project_root / msg["markdown_path"]
        md_path = write_mail(mail, mails_dir, project_root)
        if old != md_path:
            old.unlink(missing_ok=True)
            old.with_suffix(".html").unlink(missing_ok=True)
        db.record_message(
            uid, folder=msg["folder"], subject=mail.subject, sender=mail.sender,
            recv_date=msg["recv_date"], markdown_path=str(md_path.relative_to(project_root)),
        )
        db.mark_large_link_checked(uid)
        reread += 1
        log.info("large-link backfill: uid=%s now lists %d attachment(s)", uid, len(mail.attachments))
    return reread


def with_bcc_line(md: str, bcc: str) -> str:
    """``md`` with a ``bcc:`` front-matter line after ``cc:``; unchanged when it has one or no front matter."""
    lines = md.split("\n")
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return md
    end = lines.index("---", 1)
    front = lines[1:end]
    if any(line.startswith("bcc:") for line in front):
        return md
    at = next((i + 1 for i, line in enumerate(front) if line.startswith("cc:")), len(front))
    front.insert(at, f"bcc: {_yaml_escape(bcc)}")
    return "\n".join(["---", *front, *lines[end:]])


def backfill_bcc(
    db: StateDB, scraper: InboxScraper, project_root: Path, *, folder_uids: dict[str, str] | None = None,
) -> int:
    """Fill the Bcc of saved sent mails, newest first. Returns the number of mails filled."""
    folder_uid = (folder_uids or {}).get("sent")
    if not folder_uid:  # the sent folder was not resolved this run; try again next run
        return 0
    rows = [row for row in (db.message_row(uid) for uid in db.existing_uids("sent")) if row]
    rows.sort(key=lambda row: row.get("recv_date") or "", reverse=True)
    tried = filled = 0
    for msg in rows:
        md = project_root / (msg.get("markdown_path") or "")
        if not msg.get("markdown_path") or not md.is_file():
            continue
        text = md.read_text(encoding="utf-8")
        if with_bcc_line(text, "") == text:
            continue
        if tried >= BCC_BACKFILL_MAX_PER_RUN:
            break
        tried += 1
        try:
            bcc = parse_view_async_html(scraper.fetch_mail_html(msg["uid"], folder_uid))["bcc"].strip()
        except Exception as e:  # the next run tries again
            log.warning("bcc backfill: re-read failed uid=%s: %s", msg["uid"], type(e).__name__)
            continue
        md.write_text(with_bcc_line(text, bcc), encoding="utf-8")
        filled += 1
    if filled:
        log.info("bcc backfill: filled %d sent mail(s)", filled)
    return filled
