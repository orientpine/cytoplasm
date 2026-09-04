#!/usr/bin/env python3
"""budget skill CLI (W4-3): `!budget` query is gate-free read-only; the
change-detection pipeline drafts a request mail and sends ONLY after the
소유자 확정 게이트를 통과한 뒤에만 발송한다 (제약 1).

Exit codes: 0 ok | 1 approval absent/invalid (nothing sent) | 2 input rejected
            3 config/env error | 4 sheet access failed (error surfaced +
            retry queued) | 6 send execution failed (recorded as failed)

Env: BUDGET_DB (~/state/budget.db), BUDGET_GATE_DIR (~/.hermes/budget-gate),
     BUDGET_APPROVAL_LOG (/srv/autophagy-agents/logs/approvals.jsonl),
     BUDGET_CONFIG (~/.hermes/budget/config.json — mail_to), BUDGET_GWS_BIN,
     BUDGET_SHEET_ID, BUDGET_SHEETS_FILE (과제×년도 레지스트리), BUDGET_SHEET_FILE
     (fixture), AUTOPHAGY_REPO_ROOT,
     INTEROP_RUNTIME, INTEROP_CONFIG, E2E_TEST_MODE, INTEROP_E2E_SECRET,
     DISCORD_BOT_TOKEN (production posting/reaction path).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import budget_approval
import budget_confirm
import budget_core
import budget_gate
import budget_governed
import budget_registry
import budget_store


class SheetAccessError(RuntimeError):
    """Sheet read failed (gws error / network) — retry-queue path."""


def _db_path() -> Path:
    return Path(os.environ.get("BUDGET_DB", "~/state/budget.db")).expanduser()


def _sheet_id() -> str:
    sheet_id = os.environ.get("BUDGET_SHEET_ID", "").strip()
    if not sheet_id:
        raise budget_gate.GateError("BUDGET_SHEET_ID가 없습니다 (fail-closed)", 3)
    return sheet_id


def read_balance_values(sheet_id: str = "") -> list[list[str]]:
    fixture = os.environ.get("BUDGET_SHEET_FILE", "")
    if fixture:
        try:
            raw = Path(fixture).read_text(encoding="utf-8")
        except OSError as error:
            raise SheetAccessError(f"fixture 읽기 실패: {error}") from None
        return budget_core.parse_balance_payload(raw)
    result = subprocess.run(  # noqa: S603 — frozen read-only argv
        [budget_gate.gws_bin(), "sheets", "+read", "--spreadsheet", sheet_id or _sheet_id(),
         "--range", budget_core.BALANCE_READ_RANGE],
        capture_output=True, text=True, timeout=budget_gate.GWS_TIMEOUT_S, check=False,
        cwd=str(Path.home()),
    )
    if result.returncode != 0:
        raise SheetAccessError(
            f"gws sheets read 실패 rc={result.returncode}: {result.stderr.strip()[:200]}"
        )
    return budget_core.parse_balance_payload(result.stdout)


def _selected_ref(args: argparse.Namespace) -> tuple[str, budget_registry.SheetRef | None]:
    refs = budget_registry.active_refs()
    if refs is None:
        if getattr(args, "project", "") or getattr(args, "year", 0):
            raise budget_gate.GateError(
                "--project/--year는 레지스트리(BUDGET_SHEETS_FILE)가 있을 때만 사용 가능", 3
            )
        return os.environ.get("BUDGET_SHEET_ID", "").strip(), None
    ref = budget_registry.select(
        refs, project=getattr(args, "project", ""), year=getattr(args, "year", 0)
    )
    return ref.sheet_id, ref


def _sheet_label(ref: budget_registry.SheetRef | None) -> str:
    return f" sheet={ref.sheet_key}" if ref else ""


def cmd_query(args: argparse.Namespace) -> int:
    sheet_id, ref = _selected_ref(args)
    values = read_balance_values(sheet_id)
    budget_core.validate_header(values)
    rows = budget_core.data_rows(values)
    selected = [row for row in rows if not args.item or row[0] == args.item]
    if args.item and not selected:
        known = ", ".join(row[0] for row in rows)
        print(f"NOT-FOUND item={args.item} (알려진 항목: {known})", file=sys.stderr)
        return 2
    for row in selected:
        print("ROW " + json.dumps(
            dict(zip(budget_core.HEADER_EXPECTED, row)), ensure_ascii=False, sort_keys=True
        ))
    print(
        f"BUDGET-OK n={len(selected)} "
        f"sheet_sha={budget_core.snapshot_hash(rows)[:12]}{_sheet_label(ref)}"
    )
    return 0


def _post_draft_for_approval(draft: dict) -> str:
    """Post through the shared lifecycle: one live request per budget:{mail_to}."""
    return budget_approval.post_for_approval(draft)


_DM_NOTIFIABLE_METHODS = frozenset({"manual_reaction"})


def _notify_sent(draft: dict, method: str) -> None:
    """Best-effort send-result notice — mail과 동일 프로세스 (2026-08-23).

    승인 provenance allowlist로 게이트된다: 주입·테스트 승인(E2E)은 실제 통지를
    열지 않는다(mail `_notify_sent` 선례). 어떤 실패도 tick을 죽이지 않는다.
    """
    if method not in _DM_NOTIFIABLE_METHODS:
        print(f"NOTIFY-SKIP draft={draft['id']} reason={method}", file=sys.stderr)
        return
    try:
        budget_confirm.notify_result(
            draft,
            f"✉️ 발송 완료: {draft['subject']} → {draft['mail_to']} (draft {draft['id']})\n"
            "소유자 ✅ 승인으로 발송되었습니다.",
            outcome=budget_confirm.OUTCOME_DONE,
        )
    except Exception as error:  # noqa: BLE001 — notification must never break the tick
        print(f"NOTIFY-FAIL draft={draft['id']} "
              f"err={budget_core.redact(str(error))[:120]}", file=sys.stderr)


def _notify_cancelled(draft: dict) -> None:
    """Best-effort cancel notice — the discard is already committed."""
    try:
        budget_confirm.notify_result(
            draft,
            f"⛔ 발송 취소: {draft['subject']} → {draft['mail_to']} (draft {draft['id']})\n"
            "소유자 ⛔ 리액션으로 취소되어 메일은 발송되지 않았습니다.",
            outcome=budget_confirm.OUTCOME_CANCELLED,
        )
    except Exception as error:  # noqa: BLE001 — notification must never break the tick
        print(f"NOTIFY-FAIL draft={draft['id']} "
              f"err={budget_core.redact(str(error))[:120]}", file=sys.stderr)


def _snapshot_one(
    db: Path, *, sheet_id: str, ref: budget_registry.SheetRef | None, post: bool,
    origin_channel_id: str, origin_message_id: str, resolve: bool,
) -> None:
    label = _sheet_label(ref)
    sheet_key = ref.sheet_key if ref else ""
    values = read_balance_values(sheet_id)
    budget_core.validate_header(values)
    if resolve:
        resolved = budget_store.resolve_retries(db, budget_core.utc_now())
        if resolved:
            print(f"RETRY-RESOLVED n={resolved}")
    rows = budget_core.data_rows(values)
    new_hash = budget_core.snapshot_hash(rows)
    last = budget_store.latest_snapshot(db, sheet_key=sheet_key)
    if last is None:
        budget_store.store_snapshot(
            db, new_hash, rows, budget_core.utc_now(), sheet_key=sheet_key
        )
        print(f"BASELINE stored hash={new_hash[:12]}{label}")
        return
    prev_hash, prev_rows = last
    if prev_hash == new_hash:
        print(f"NO-CHANGE hash={new_hash[:12]}{label}")
        return
    changes = budget_core.diff_rows(prev_rows, rows)
    change_key = budget_core.claim_key(prev_hash, new_hash, sheet_key=sheet_key)
    if not budget_store.claim_change(db, change_key, budget_core.utc_now()):
        budget_store.store_snapshot(
            db, new_hash, rows, budget_core.utc_now(), sheet_key=sheet_key
        )
        print(f"ALREADY-CLAIMED key={change_key} (스냅샷만 전진, 초안 중복 없음)")
        return
    subject, body = budget_core.render_mail(
        changes, prev_hash=prev_hash, new_hash=new_hash, now=datetime.now(UTC),
        context=sheet_key,
    )
    try:
        draft = budget_gate.create_draft(
            changes=changes, subject=subject, body=body, recipient=budget_gate.mail_to(),
            prev_hash=prev_hash, new_hash=new_hash, claim_key=change_key,
            origin_channel_id=origin_channel_id, origin_message_id=origin_message_id,
            project=ref.project if ref else "", year=ref.year if ref else 0,
        )
    except OSError:
        budget_store.release_change(db, change_key)
        raise
    budget_store.store_snapshot(db, new_hash, rows, budget_core.utc_now(), sheet_key=sheet_key)
    if not post:
        content = budget_core.render_approvals_message(draft)
        print(f"DRAFT-CREATED id={draft['id']} sha256={draft['sha256']} "
              f"changes={len(changes)} message=unposted")
        print(content)
        return
    message_id = _post_draft_for_approval(draft)
    budget_gate.set_message_id(draft, message_id)
    print(f"DRAFT-CREATED id={draft['id']} sha256={draft['sha256']} "
          f"changes={len(changes)} message={message_id}")


def _queue_sheet_failure(db: Path, sheet_key: str, error: Exception) -> None:
    reason = budget_core.redact(str(error))[:300]
    if sheet_key:
        reason = f"[{sheet_key}] {reason}"
    retry_id = budget_store.queue_retry(db, reason, budget_core.utc_now())
    print(f"SHEET-FAIL retry_queued id={retry_id} reason={reason}", file=sys.stderr)


def _snapshot(*, post: bool, origin_channel_id: str = "", origin_message_id: str = "") -> int:
    db = _db_path()
    refs = budget_registry.active_refs()
    if refs is None:
        try:
            _snapshot_one(
                db, sheet_id=os.environ.get("BUDGET_SHEET_ID", "").strip(), ref=None, post=post,
                origin_channel_id=origin_channel_id, origin_message_id=origin_message_id,
                resolve=True,
            )
        except (SheetAccessError, budget_core.SheetSchemaError) as error:
            _queue_sheet_failure(db, "", error)
            return 4
        return 0
    failures = 0
    for ref in refs:
        try:
            _snapshot_one(
                db, sheet_id=ref.sheet_id, ref=ref, post=post,
                origin_channel_id=origin_channel_id, origin_message_id=origin_message_id,
                resolve=False,
            )
        except (SheetAccessError, budget_core.SheetSchemaError) as error:
            _queue_sheet_failure(db, ref.sheet_key, error)
            failures += 1
    if failures:
        return 4
    resolved = budget_store.resolve_retries(db, budget_core.utc_now())
    if resolved:
        print(f"RETRY-RESOLVED n={resolved}")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    return _snapshot(
        post=not args.no_post,
        origin_channel_id=str(getattr(args, "origin_channel_id", "") or ""),
        origin_message_id=str(getattr(args, "origin_message_id", "") or ""),
    )


def cmd_watch(args: argparse.Namespace) -> int:
    """Production cron tick: repost, auto-send owner-approved drafts, snapshot."""
    if os.environ.get("E2E_TEST_MODE"):
        raise budget_gate.GateError("watch는 프로덕션 전용 — E2E_TEST_MODE 거부", 3)
    owner = budget_confirm.owner_id()
    for draft in budget_gate.list_drafts():
        if draft.get("status") != "pending":
            continue
        if not draft.get("message_id"):
            draft = budget_gate.set_message_id(draft, _post_draft_for_approval(draft))
            print(f"REPOSTED draft={draft['id']} message={draft['message_id']}")
        try:
            action = budget_confirm.resolve_reaction(draft)
        except budget_gate.GateError as error:
            if error.exit_code != 1:
                raise
            continue  # 승인 없음 → pending 유지, 발송 0
        if action == budget_confirm.CANCEL_EMOJI:
            budget_gate.discard_draft(draft["id"])
            _notify_cancelled(draft)
            print(f"CANCELLED draft={draft['id']} method=manual_reaction")
            continue
        if action != budget_confirm.APPROVE_EMOJI:
            continue
        ref = f"reaction:{draft['message_id']}"
        budget_gate.execute_draft(draft, budget_gate.Approval(
            ref=ref, method="manual_reaction", owner=owner,
        ))
        _notify_sent(draft, "manual_reaction")
        print(f"SENT draft={draft['id']} method=manual_reaction ref={ref}")
    return cmd_snapshot(args)


def cmd_confirm(args: argparse.Namespace) -> int:
    draft = budget_gate.load_draft(args.draft)
    if args.injection_file:
        ref = budget_confirm.confirm_via_injection(draft, Path(args.injection_file))
        method = "signed_injection_e2e"
    else:
        if os.environ.get("E2E_TEST_MODE"):
            raise budget_gate.GateError(
                "E2E_TEST_MODE인데 --injection-file이 없음 — 모호한 모드 거부", 3
            )
        ref = budget_confirm.confirm_via_reaction(draft)
        method = "manual_reaction"
    budget_gate.execute_draft(draft, budget_gate.Approval(
        ref=ref, method=method, owner=budget_confirm.owner_id(),
    ))
    _notify_sent(draft, method)
    print(f"SENT draft={args.draft} method={method} ref={ref}")
    return 0


def cmd_discard(args: argparse.Namespace) -> int:
    budget_gate.discard_draft(args.draft)
    print(f"DISCARDED draft={args.draft}")
    return 0


def cmd_list_drafts(_args: argparse.Namespace) -> int:
    for record in budget_gate.list_drafts():
        print(f"DRAFT id={record['id']} status={record['status']} "
              f"message={record.get('message_id') or 'unposted'} created={record['created']}")
    return 0


def cmd_retry_queue(_args: argparse.Namespace) -> int:
    pending = budget_store.pending_retries(_db_path())
    for retry_id, reason, queued_at in pending:
        print(f"RETRY-PENDING id={retry_id} queued={queued_at} reason={reason}")
    print(f"RETRY-QUEUE pending={len(pending)}")
    return 0


def cmd_sheets(_args: argparse.Namespace) -> int:
    refs = budget_registry.active_refs()
    if refs is None:
        sheet = os.environ.get("BUDGET_SHEET_ID", "").strip()
        print(f"SHEET mode=legacy id={budget_core.mask_value(sheet)}")
        print(f"SHEETS-OK n={1 if sheet else 0} mode=legacy")
        return 0
    for ref in refs:
        print(
            f"SHEET project={ref.project} year={ref.year} "
            f"id={budget_core.mask_value(ref.sheet_id)}"
        )
    print(f"SHEETS-OK n={len(refs)} mode=registry")
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    draft = budget_gate.load_draft(args.draft)
    budget_confirm.sign_injection(
        draft, Path(args.out), args.user_id or None, args.channel_id or None,
        args.forge_signature,
    )
    print(f"SIGNED draft={args.draft} out={args.out} forged={args.forge_signature}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="budget", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    query = sub.add_parser("query", help="!budget 조회 (읽기 전용, 게이트 불요)")
    query.add_argument("--item", default="", help="항목명 필터 (예: 인건비)")
    query.add_argument("--project", default="", help="과제명 (레지스트리 모드)")
    query.add_argument(
        "--year", type=int, default=0,
        help="년도 (레지스트리 모드, 생략=해당 과제의 최신 년도)",
    )
    query.set_defaults(func=cmd_query)

    snapshot = sub.add_parser("snapshot", help="잔액 탭 스냅샷+diff (변경 시 초안+게시)")
    snapshot.add_argument("--no-post", action="store_true", help="초안만 만들고 승인 메시지 게시 생략")
    snapshot.add_argument(
        "--origin-channel-id", default="",
        help="지시를 받은 원 채널 id — 발송/취소 결과를 이 채널의 스레드로 통지",
    )
    snapshot.add_argument(
        "--origin-message-id", default="",
        help="원 채널의 지시 메시지 id — 있으면 그 메시지에 결과 스레드를 앵커",
    )
    snapshot.set_defaults(func=cmd_snapshot)

    watch = sub.add_parser("watch", help="프로덕션 cron tick: 승인건 발송 + 스냅샷")
    watch.add_argument("--no-post", action="store_true", help=argparse.SUPPRESS)
    watch.set_defaults(func=cmd_watch)

    confirm = sub.add_parser("confirm", help="소유자 승인 검증 후에만 발송")
    confirm.add_argument("--draft", required=True)
    confirm.add_argument("--injection-file", default="")
    confirm.set_defaults(func=cmd_confirm)

    discard = sub.add_parser("discard", help="초안 폐기 (승인 거부)")
    discard.add_argument("--draft", required=True)
    discard.set_defaults(func=cmd_discard)

    sub.add_parser("list-drafts", help="초안 목록").set_defaults(func=cmd_list_drafts)
    sub.add_parser("retry-queue", help="재시도 큐 상태").set_defaults(func=cmd_retry_queue)
    sub.add_parser("sheets", help="활성 시트 목록 (ID 마스킹)").set_defaults(func=cmd_sheets)

    sign = sub.add_parser("sign", help="E2E 전용: 서명된 주입 승인 생성")
    sign.add_argument("--draft", required=True)
    sign.add_argument("--out", required=True)
    sign.add_argument("--user-id", default="")
    sign.add_argument("--channel-id", default="")
    sign.add_argument("--forge-signature", action="store_true")
    sign.set_defaults(func=cmd_sign)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        # 상태 조회는 낡은 사본에서도 안전하지만, 나머지는 외부효과가 있어 배포본만 허용한다.
        read_only = {"query", "list-drafts", "retry-queue", "sheets"}
        if args.command not in read_only:
            message = budget_governed.refusal(Path(__file__))
            if message:
                print(message, file=sys.stderr)
                return 3
        return int(args.func(args))
    except (SheetAccessError, budget_core.SheetSchemaError) as error:
        print(f"SHEET-FAIL {budget_core.redact(str(error))[:200]}", file=sys.stderr)
        return 4
    except budget_gate.GateError as error:
        print(f"GATE-REFUSED {error}", file=sys.stderr)
        return error.exit_code
    except FileNotFoundError as error:
        print(f"GATE-REFUSED 파일 없음: {error.filename}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
