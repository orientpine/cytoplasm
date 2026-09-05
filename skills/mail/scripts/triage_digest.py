"""Daily owner digest engine for the mail triage pipeline (W4-6).

One digest run composes the existing W4-2 building blocks: the W4-1 wrapper
transport lists/reads mail, the deterministic sensitivity gate runs FIRST on
subject+sender+full body (constraint 6 — the routing guard inside ``triage_llm``
then keeps gate-hit mail on the approved Codex OAuth tier), classification
reuses the ``_process_one`` composition, and important+schedule mail is
delegated to the calendar skill (draft only — its own gate confirms).

Constraint 7 is realized as the delivery-vs-store split: the private owner
message carries the real subject and summary, while the persisted
``digest_items`` row keeps only the masked subject and an empty summary for
sensitive mail. The run is recorded ONLY after delivery, so a failure leaves
every mail undigested for the next tick.

Env (digest-specific): TRIAGE_DIGEST_PROMPT (defaults to
prompts/digest-summary-v1.md) on top of the shared triage env described in
``triage_cli``.
"""

from __future__ import annotations

import re

from dataclasses import replace

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import triage_confirm
import triage_approval
import triage_core
import triage_gate
import triage_llm
import triage_llm_routing
import triage_sensitivity
import triage_recipient
import triage_store
import triage_transport

SUMMARY_FALLBACK = "(요약 실패)"
_CATEGORY_BADGES = {"important": "🔴 중요", "normal": "🔵 일반", "spam": "🗑️ 스팸"}
_FLAG_BADGES = {
    "reply_needed": "↩️ 회신 필요",
    "schedule_needed": "📅 일정",
    "budget": "💳 예산",
    "cc": "👀 참조(CC)",
    "classification_failed": "⚠️ 분류 실패",
}
_SENSITIVE_BADGE = "🔒 민감"
_CLASSIFY_FAILED_FLAG = "classification_failed"
# Fail-open classification: surface the mail conservatively as 중요 with NO
# actionable flags, so one unparseable/slow model response never strands the
# whole digest and never delegates a calendar draft off a fabricated verdict.
_CLASSIFY_FALLBACK = triage_core.Classification(
    category="important",
    reply_needed=False,
    schedule_needed=False,
    budget=False,
    schedule_text="",
    reason="classification_unavailable",
)
_MARKDOWN_ESCAPE = re.compile(r"([\\*_~`|\[\]])")


def _footer() -> str:
    instruction = triage_approval.reaction_instruction(
        {"kind": "reply", "surface": "owner-dm"},
        name_surface=True,
    )
    return (
        "---\n"
        '💬 회신 지시 · "N번 메일, …라고 회신해줘"라고 말하면 초안을 만듭니다\n'
        f"초안이 만들어지면 {instruction}"
    )


def select_new_mails(
    mails: list[dict], digested: set[str], processed: set[str]
) -> list[dict]:
    """Keep wrapper-listed mails seen in no earlier digest/triage, oldest first.

    Pure: excludes uids present in either set and sorts ascending by the
    wrapper `date` field (ISO-8601 recv_date — lexicographic sort is
    chronological), stable for equal dates.
    """
    known = digested | processed
    fresh = [
        mail
        for mail in mails
        if str(mail.get("uid") or "") and str(mail.get("uid")) not in known
    ]
    return sorted(fresh, key=lambda mail: str(mail.get("date") or ""))


def build_item(
    mail_detail: dict, item_no: int, *, rules: tuple[triage_sensitivity.TagRule, ...],
) -> tuple[dict, dict]:
    """Gate → classify → summarize one mail; return ``(owner_item, store_item)``.

    ``owner_item`` carries the real subject/summary for the owner message;
    ``store_item`` follows the ``digest_items`` contract and, when the gate
    hits, persists only the masked subject and an empty summary (constraint 7).
    Both LLM steps are retried once, then fall back per item: a summarize failure
    keeps the item with a fallback summary and records the masked reason via
    ``triage_llm.log_failure`` (the cron drops stderr, so that line is the only
    trace), and a classify failure falls open to a conservative 중요 verdict with
    NO actionable flags plus a ``classification_failed`` marker (fail-open
    listing — one bad mail must not silently strand the whole digest).

    A ``LlmUnavailableError`` is NOT one bad mail: the Codex OAuth tier itself
    cannot answer, and no other tier exists to answer for it. It is logged and
    re-raised so ``run_digest`` fails the whole tick closed instead of composing
    a digest of placeholders (2026-09-04 migration — the old GLM degrade path).
    """
    uid = str(mail_detail.get("uid") or "")
    subject = str(mail_detail.get("subject") or "")
    sender = str(mail_detail.get("sender") or "")
    body = str(mail_detail.get("body") or "")
    uid_opaque = triage_core.mask_value(uid)
    gate = triage_sensitivity.evaluate("\n".join((subject, sender, body)), rules)  # ① FIRST

    def classify_step() -> tuple[triage_core.Classification, str]:
        return triage_llm.classify(  # ② guarded by the step-① verdict
            subject=subject, sender=sender, body=body, sensitive=gate.sensitive,
            uid_opaque=uid_opaque,
            prompt_path=triage_transport._env_path(
                "TRIAGE_CLASSIFY_PROMPT",
                str(triage_transport.SKILL_DIR / "prompts/triage-classify-v1.md"),
            ),
        )

    try:
        cls, _provider = triage_llm_routing.call_with_retry(
            classify_step,
            retry_on=(triage_llm.LlmCallError, triage_core.LlmParseError),
        )
    except triage_llm.LlmUnavailableError as error:  # tier down — never a per-item fallback
        triage_llm.log_failure(
            purpose="classify", uid_opaque=uid_opaque,
            sensitive=gate.sensitive, error=error,
        )
        raise
    except (triage_llm.LlmCallError, triage_core.LlmParseError) as error:
        cls, classify_failed = _CLASSIFY_FALLBACK, True
        triage_llm.log_failure(
            purpose="classify", uid_opaque=uid_opaque,
            sensitive=gate.sensitive, error=error,
        )
    else:
        classify_failed = False

    def summarize_step() -> str:
        return triage_llm.summarize(  # constraint-6 guard lives inside summarize
            subject=subject, sender=sender, body=body, sensitive=gate.sensitive,
            uid_opaque=uid_opaque,
            prompt_path=triage_transport._env_path(
                "TRIAGE_DIGEST_PROMPT",
                str(triage_transport.SKILL_DIR / "prompts/digest-summary-v1.md"),
            ),
        )

    try:
        summary = triage_llm_routing.call_with_retry(summarize_step, retry_on=(Exception,))
    except triage_llm.LlmUnavailableError as error:  # tier down — never a per-item fallback
        triage_llm.log_failure(
            purpose="digest_summary", uid_opaque=uid_opaque,
            sensitive=gate.sensitive, error=error,
        )
        raise
    except Exception as error:  # noqa: BLE001 — fail-open listing: keep the item, mark the failure
        summary = SUMMARY_FALLBACK
        triage_llm.log_failure(  # cron drops stderr — the log line is the only trace
            purpose="digest_summary", uid_opaque=uid_opaque,
            sensitive=gate.sensitive, error=error,
        )
    owner = triage_recipient.owner_address()
    role = triage_recipient.recipient_role(body, owner)
    _to_addresses, cc_addresses = triage_recipient.parse_recipients(body)
    cc_display = ""
    if body.startswith("---"):
        for line in body.split("---", 2)[1].splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip().lower() == "cc":
                cc_display = value.strip().strip("'\"")
                break
    if role == "cc" and cls.reply_needed:  # 참조 수신 — 회신 대상 아님 (owner 2026-07-19)
        cls = replace(cls, reply_needed=False)
    mass_notice = bool(re.search(r"(?:^|[<\s])no-?reply@", sender, re.IGNORECASE)) and any(
        marker in f"{subject}\n{body}".lower()
        for marker in ("newsletter", "뉴스레터", "bulk", "distribution", "수신 거부", "구독 해지")
    )
    if role == "to" and mass_notice and cls.reply_needed:
        cls = replace(cls, reply_needed=False)
    flags = cls.flags() + (("cc",) if role == "cc" else ())
    if classify_failed:
        flags = flags + (_CLASSIFY_FAILED_FLAG,)
    note = ""
    if cls.category == "important" and cls.schedule_needed and cls.schedule_text:
        note = triage_transport._delegate_schedule(cls.schedule_text, uid_opaque)
    shared = {
        "item_no": item_no,
        "uid": uid,
        "sender_masked": triage_core.mask_value(sender),
        "sensitive": int(gate.sensitive),
        "category": cls.category,
        "note": note,
        "recv_date": str(mail_detail.get("date") or ""),
    }
    dm_item = {
        **shared,
        "subject": subject,
        "sender": sender,
        "cc": cc_display if role != "unknown" else cc_addresses,
        "summary": summary,
        "flags": flags,
    }
    store_item = {
        **shared,
        "subject": triage_core.mask_value(subject) if gate.sensitive else subject,
        "summary": "" if gate.sensitive else summary,
        "flags": ",".join(flags),
    }
    return dm_item, store_item


def _sanitize_inline(text: str) -> str:
    """One-line, markdown-inert projection of mail-derived text for the DM.

    Collapses whitespace/newlines, backslash-escapes Discord markdown, and
    inserts U+200B after every ``@`` so ``@everyone``/``@here``/``<@id>``
    can never ping from a digest card.
    """
    flat = " ".join(text.split())
    return _MARKDOWN_ESCAPE.sub(r"\\\1", flat).replace("@", "@\u200b")


def _sanitize_contact(text: str) -> str:
    flat = _MARKDOWN_ESCAPE.sub(r"\\\1", " ".join(text.split()))
    flat = re.sub(
        r"@(everyone|here)\b",
        lambda matched: f"@\u200b{matched.group(1)}",
        flat,
        flags=re.IGNORECASE,
    )
    return flat.replace("<@", "<@\u200b")


def _recv_kst(recv_date: str) -> str:
    """``수신 MM-DD HH:MM`` KST segment; '' when unparseable (fail-safe)."""
    try:
        parsed = datetime.fromisoformat(recv_date.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)  # wrapper dates are UTC
    return parsed.astimezone(ZoneInfo("Asia/Seoul")).strftime("수신 %m-%d %H:%M")


def _badge_line(item: dict) -> str:
    """Category → sensitivity → flags as Korean emoji badges (raw-key fail-safe)."""
    badges = [_CATEGORY_BADGES.get(str(item["category"]), str(item["category"]))]
    if item["sensitive"]:
        badges.append(_SENSITIVE_BADGE)
    badges.extend(_FLAG_BADGES.get(str(flag), str(flag)) for flag in item["flags"])
    return " · ".join(badges)


def render_digest_dm(dm_items: list[dict], *, kst_now: datetime) -> str:
    """Render the owner digest as Discord Markdown cards (pinned format).

    Card per mail: ``### N. 제목`` heading, Korean emoji badge line, blockquote
    summary, KST receive time + inline-code UID/masked sender, optional calendar
    note — then a ``---`` separator and the reply-instruction footer. Mail-derived
    text (subject/summary) is markdown-escaped and mention-neutralized.
    """
    stamp = kst_now.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M")
    lines = ["## 📬 기관메일 다이제스트", f"{stamp} KST · 신규 {len(dm_items)}건"]
    if not dm_items:
        lines.extend(["", "신규 메일 없음"])
        return "\n".join(lines)
    for item in dm_items:
        lines.append("")
        lines.append(f"### {item['item_no']}. {_sanitize_inline(str(item['subject']))}")
        lines.append(_badge_line(item))
        lines.append(f"> 요약 · {_sanitize_inline(str(item['summary']))}")
        received = _recv_kst(str(item["recv_date"]))
        meta = f"`UID {item['uid']}` · 발신(마스킹) `{item['sender_masked']}`"
        lines.append(f"{received} · {meta}" if received else meta)
        sender = str(item.get("sender") or "")
        cc = str(item.get("cc") or "")
        if sender:
            lines.append(f"발신 · {_sanitize_contact(sender)}")
        if cc:
            lines.append(f"참조(CC) · {_sanitize_contact(cc)}")
        if item["note"]:
            note = str(item["note"]).replace("`", "'")
            lines.append(f"🗓️ 일정 초안 `{note}`")
    lines.extend(["", _footer()])
    return "\n".join(lines)


def _fail_marker(stage: str, code: str, error: BaseException) -> str:
    """One redacted, single-line machine marker for a digest failure.

    The cron watcher keys retry/alert policy on ``stage``/``code``/``retry_safe``
    fields, never on free-form prose. ``detail`` is redacted (emails, long
    digits) and flattened so no mail content, address, or token can leak into
    the owner failure alert. Both current failure boundaries are retry-unsafe:
    a build-stage item may already have delegated a calendar draft, and a
    delivery failure may have sent some Discord chunks.
    """
    detail = triage_core.redact(str(error)).replace("\n", " ")[:200]
    return f"DIGEST-FAIL stage={stage} retry_safe=false code={code} detail={detail}"


def run_digest(*, limit: int, sync: bool, dry_run: bool) -> int:
    """One digest tick: list → select → build → DM first → record after.

    Zero new mail still sends the (empty) digest DM and records item_count=0.
    A sync gate failure falls back to local DB data and marks the DM/dry-run body
    with a warning that mailon reauthentication may be needed.
    Any build-stage or delivery failure raises a single redacted structured
    ``DIGEST-FAIL`` marker (see ``_fail_marker``) before ``record_digest_run``,
    leaving every listed mail undigested so the next tick retries. An
    unavailable Codex OAuth tier gets its own ``code=codex_unavailable`` marker:
    with no second tier to degrade to, the tick fails closed rather than
    delivering a digest of placeholders.
    """
    rules = triage_sensitivity.load_rules(triage_transport._rules_path())
    db = triage_gate.db_path()
    digested = triage_store.digested_uids(db)
    processed = {row[0] for row in triage_store.processed_rows(db)}
    sync_failed = False
    try:
        mails = triage_transport._list_mails(limit, sync)
    except triage_gate.GateError:
        if not sync:
            raise
        mails = triage_transport._list_mails(limit, False)
        sync_failed = True
    selected = select_new_mails(mails, digested, processed)
    dm_items: list[dict] = []
    store_items: list[dict] = []
    for item_no, mail in enumerate(selected, start=1):
        uid = str(mail.get("uid") or "")
        detail = {**mail, **triage_transport._get_mail(uid), "uid": uid}
        try:
            dm_item, store_item = build_item(detail, item_no, rules=rules)
        except triage_llm.LlmUnavailableError as error:  # fail closed — no downgraded digest
            raise triage_gate.GateError(
                _fail_marker("build", "codex_unavailable", error), 4
            ) from error
        except Exception as error:  # noqa: BLE001 — cron alert needs one structured marker
            raise triage_gate.GateError(_fail_marker("build", "llm_call_failed", error), 4) from error
        dm_items.append(dm_item)
        store_items.append(store_item)
    body = render_digest_dm(dm_items, kst_now=datetime.now(ZoneInfo("Asia/Seoul")))
    if sync_failed:
        body = "⚠️ mailon 동기화 실패 — 로컬 DB 기준 (재인증 필요할 수 있음)\n" + body
    if dry_run:
        print(body)
        print(f"DIGEST dry-run items={len(dm_items)}")
        return 0
    try:
        triage_confirm.dm_owner(body)  # Delivery first — failure leaves every mail undigested
    except Exception as error:  # noqa: BLE001 — cron alert needs one structured marker
        raise triage_gate.GateError(
            _fail_marker("deliver", "discord_delivery_failed", error), 4
        ) from error
    run_id = triage_store.record_digest_run(db, triage_core.utc_now(), store_items)
    print(f"DIGEST run={run_id} items={len(dm_items)}")
    return 0
