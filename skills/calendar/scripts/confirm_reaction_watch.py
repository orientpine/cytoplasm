#!/usr/bin/env python3
"""Poll owner-only reactions on pending standalone calendar confirmation DMs."""
from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Final, Protocol, assert_never
from urllib.error import HTTPError, URLError

_ENV_SECRETS: Final = Path.home() / ".env.secrets"

# 마운트 판정은 governed live 정의 하나(automation/skill_mount.py)에서만 온다. 노드에서
# 이 워처는 ~/.hermes/scripts/ 에 평평하게 배포되므로 코드 루트를 값으로 되짚는다 —
# 체크아웃 → 릴리스 current → 미러 (test_skill_runtime_root_fallback.py 와 같은 관용구).
for _root in (
    *Path(__file__).resolve().parents,
    Path(os.environ.get("AUTOPHAGY_RUNTIME_ROOT") or "/srv/autophagy-agent-current"),
    Path("/srv/autophagy-agents"),
):
    if (_root / "automation" / "skill_mount.py").is_file():
        sys.path.insert(0, str(_root))
        break
from automation.skill_mount import skill_scripts  # noqa: E402 — 코드 루트 확정 뒤에만 가능하다


def _load_env_secrets(path: Path = _ENV_SECRETS) -> None:
    """no-agent cron hands the wrapper no secrets, so the parent loads them itself.

    This watcher reads Discord reactions in-process and spawns `calendar_cli.py`, both
    of which need credentials. Measured 2026-08-18 on `budget-watch`: without this the
    configuration sits on disk and never reaches the code that needs it (규약 (b)).
    Runs at import time because the module-level `import_module` calls below already
    touch configuration. Inventory check: tests/unit/test_watcher_secret_propagation.py.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


_load_env_secrets()

_SCRIPTS = skill_scripts("calendar", env_var="CALENDAR_SCRIPTS")
if _SCRIPTS.exists() and str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

calendar_confirm = import_module("calendar_confirm")
calendar_approval = import_module("calendar_approval")
calendar_gate = import_module("calendar_gate")
_pending = import_module("calendar_pending")
PendingConfirm = _pending.PendingConfirm
PendingConfirmStore = _pending.PendingConfirmStore
_commands = import_module("calendar_watch_commands")
_diagnostics = import_module("calendar_watch_diagnostics")
subprocess = _commands.subprocess
CliCommands = _commands.CliCommands
ConfirmBatchError = _diagnostics.ConfirmBatchError
ConfirmWatchError = _diagnostics.ConfirmWatchError
_log_failure = _diagnostics.log_failure
_redact = _diagnostics.redact
_transport_failure = _diagnostics.transport_failure

APPROVE_EMOJI = "\u2705"
CANCEL_EMOJI = "\u26d4"
EXPIRY = timedelta(hours=24)
class DiscordClient(Protocol):
    def message_content(self, entry: PendingConfirm) -> str | None: ...

    def reaction_users(self, entry: PendingConfirm, emoji: str) -> tuple[Mapping[str, str | bool], ...]: ...

    def send_owner_dm(self, content: str) -> None: ...


class CommandRunner(Protocol):
    def confirm(self, entry: PendingConfirm, owner_id: str) -> None: ...

    def discard(self, draft_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class DiscordApi:
    owner_id: str

    def message_content(self, entry: PendingConfirm) -> str | None:
        """Read the posted confirmation DM before accepting its reaction."""
        try:
            return calendar_confirm.confirmation_message_content(entry)
        except HTTPError as error:
            if error.code == 404:
                return None
            raise _transport_failure("confirmation DM read failed", "discord.message", error) from error
        except (calendar_gate.GateError, URLError, OSError) as error:
            raise _transport_failure("confirmation DM read failed", "discord.message", error) from error

    def reaction_users(self, entry: PendingConfirm, emoji: str) -> tuple[Mapping[str, str | bool], ...]:
        """Read all reaction users; unavailable reactions are an empty set."""
        try:
            return calendar_confirm.confirmation_reaction_users(entry, emoji)
        except (calendar_gate.GateError, HTTPError, URLError, OSError) as error:
            raise _transport_failure("reaction query failed", "discord.reactions", error) from error

    def send_owner_dm(self, content: str) -> None:
        """Send a terse owner notification after cancellation or expiry."""
        try:
            calendar_confirm.send_owner_dm(self.owner_id, content)
        except (calendar_gate.GateError, HTTPError, URLError, OSError) as error:
            raise _transport_failure("owner DM notification failed", "discord.notify", error) from error


_ACTION_LABELS: Final = {"create": "등록", "update": "수정", "delete": "삭제"}


def _load_draft_record(draft_id: str) -> dict:
    """Read the pending draft for result routing; an unreadable draft means 'no origin'."""
    try:
        record = calendar_gate.load_draft(draft_id)
    except (calendar_gate.GateError, OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def _action_label(record: Mapping[str, object]) -> str:
    return _ACTION_LABELS.get(str(record.get("action", "")), "변경")


def _executed_notice(record: Mapping[str, object], draft_id: str) -> str:
    """Result wording carrying only the action kind and draft id (SKILL.md 반출 금지)."""
    return f"✅ 캘린더 {_action_label(record)} 실행 완료 (draft {draft_id}) — 소유자 ✅ 승인"


def _cancelled_notice(record: Mapping[str, object], draft_id: str) -> str:
    return (
        f"⛔ 캘린더 {_action_label(record)} 취소 (draft {draft_id}) — "
        "소유자 ⛔ 리액션으로 취소되었습니다."
    )


def _expired_notice(record: Mapping[str, object], draft_id: str) -> str:
    return (
        f"⌛ 캘린더 {_action_label(record)} 만료 취소 (draft {draft_id}) — "
        "확정 시간이 지나 취소되었습니다."
    )


def _orphan_notice(record: Mapping[str, object], draft_id: str) -> str:
    return (
        f"🧹 캘린더 {_action_label(record)} 초안 자동 정리 (draft {draft_id}) — "
        "승인 요청 DM이 게시되지 않은 채 24시간이 지나 폐기했습니다. 필요하면 다시 요청해 주세요."
    )


@dataclass(frozen=True, slots=True)
class OwnerDecision:
    entry: PendingConfirm
    owner_id: str
    discord: DiscordClient
    commands: CommandRunner
    draft_sha256: Callable[[str], str]
    store: PendingConfirmStore
    draft_record: Callable[[str], dict] = _load_draft_record

    def probe(self, _request):
        if self.draft_sha256(self.entry.draft_id) != self.entry.sha256:
            raise ConfirmWatchError("draft hash mismatch")
        return calendar_approval.probe_entry(self.entry, self.owner_id, self.discord)

    def apply(self, _request, decision) -> None:
        state = calendar_approval.lifecycle().Probe
        record = self.draft_record(self.entry.draft_id)  # 실행/폐기 전에 읽어야 남아 있다
        match decision:
            case state.APPROVED:
                self.commands.confirm(self.entry, self.owner_id)
                if _has_thread(record):
                    # 승인 스레드(또는 채널 지시)가 있는 건에만 결과를 돌려주고 그 스레드를
                    # 닫는다 — 둘 다 없는 옛 초안은 종전대로 무통지.
                    _notify_thread(
                        record,
                        _executed_notice(record, self.entry.draft_id),
                        calendar_confirm.OUTCOME_DONE,
                    )
            case state.CANCELLED:
                self.commands.discard(self.entry.draft_id)
                _notify_result(
                    self.discord, record, _cancelled_notice(record, self.entry.draft_id),
                    calendar_confirm.OUTCOME_CANCELLED,
                )
            case unreachable:
                assert_never(unreachable)

    def drop(self, _request) -> None:
        self.store.drop(self.entry)


def run_once(
    *,
    store: PendingConfirmStore,
    owner_id: str,
    discord: DiscordClient,
    commands: CommandRunner,
    draft_sha256: Callable[[str], str],
    now: datetime,
    reminder_config: object | None = None,
    draft_record: Callable[[str], dict] = _load_draft_record,
) -> None:
    """Apply only unambiguous bound owner reactions and retain uncertain entries."""
    snapshot = store.load()
    failures: list[ConfirmWatchError] = []
    _process_entries(
        snapshot, store, owner_id, discord, commands, draft_sha256, now,
        reminder_config=reminder_config, failures=failures, draft_record=draft_record,
    )
    sweep_orphan_drafts(snapshot, commands=commands, discord=discord, now=now)
    fatal_failures = tuple(error for error in failures if error.fatal)
    if fatal_failures:
        raise ConfirmBatchError(fatal_failures)


def sweep_orphan_drafts(
    snapshot: tuple[PendingConfirm, ...],
    *,
    commands: CommandRunner,
    discord: DiscordClient,
    now: datetime,
    list_drafts: Callable[[], list[dict]] | None = None,
) -> tuple[str, ...]:
    """Discard pending drafts whose confirmation DM was never posted (best-effort).

    draft-create 와 post-confirm 은 별개 단계다 — post-confirm 이 불리지 않으면 초안은
    pending-confirms 원장에 없어 이 워처의 어떤 경로도 다시 보지 않는다(2026-07~08
    실측 33건 누적, 전부 행사일 경과). 게시된 확인에 묶인 초안은 건드리지 않고,
    EXPIRY(24h) 유예가 지난 고아만 기존 discard 경로로 폐기한 뒤 소유자에게 알린다.
    나이를 알 수 없는 초안은 폐기하지 않는다(보존이 안전한 방향). 실패는 tick 을
    죽이지 않는다 — 다음 tick 이 다시 본다.
    """
    reader = list_drafts if list_drafts is not None else calendar_gate.list_drafts
    posted = {entry.draft_id for entry in snapshot}
    swept: list[str] = []
    try:
        records = reader()
    except Exception as error:  # noqa: BLE001 — 정리는 부가물, 판독 실패는 다음 tick 몫이다.
        print(
            f"calendar-confirm-watch orphan scan failed: {_redact(str(error))}",
            file=sys.stderr,
        )
        return ()
    for record in records:
        if not isinstance(record, dict) or record.get("status") != "pending":
            continue
        draft_id = record.get("id")
        if not isinstance(draft_id, str) or not draft_id or draft_id in posted:
            continue
        created_raw = record.get("created")
        if not isinstance(created_raw, str):
            continue
        try:
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if created.tzinfo is None or now.astimezone(UTC) - created <= EXPIRY:
            continue
        try:
            commands.discard(draft_id)
        except Exception as error:  # noqa: BLE001 — 초안 하나의 실패가 나머지를 막으면 안 된다.
            print(
                f"calendar-confirm-watch orphan discard failed draft={draft_id}: "
                f"{_redact(str(error))}",
                file=sys.stderr,
            )
            continue
        _notify_result(discord, record, _orphan_notice(record, draft_id))
        swept.append(draft_id)
    return tuple(swept)


def _process_entries(
    entries: tuple[PendingConfirm, ...],
    store: PendingConfirmStore,
    owner_id: str,
    discord: DiscordClient,
    commands: CommandRunner,
    draft_sha256: Callable[[str], str],
    now: datetime,
    *,
    reminder_config: object | None = None,
    failures: list[ConfirmWatchError] | None = None,
    draft_record: Callable[[str], dict] = _load_draft_record,
) -> tuple[PendingConfirm, ...]:
    retained: list[PendingConfirm] = []
    for entry in entries:
        try:
            decision = OwnerDecision(
                entry, owner_id, discord, commands, draft_sha256, store, draft_record
            )
            if now.astimezone(UTC) - entry.created > EXPIRY:
                with calendar_approval.confirm_lease(store.path.parent).hold(entry.key) as owned:
                    if not owned:
                        retained.append(entry)
                        continue
                    decision.probe(calendar_approval.request_of(entry))
                    record = draft_record(entry.draft_id)  # 폐기 전에 읽는다
                    commands.discard(entry.draft_id)
                    _notify_result(
                        discord, record, _expired_notice(record, entry.draft_id),
                        calendar_confirm.OUTCOME_EXPIRED,
                    )
                    decision.drop(calendar_approval.request_of(entry))
            else:
                request = calendar_approval.request_of(entry)
                lease = calendar_approval.confirm_lease(store.path.parent)
                if reminder_config is not None:
                    reminder = calendar_approval._repo_module("approval_reminder")
                    surface = calendar_approval._repo_module("approval_surface")
                    kind = surface.ApprovalKind(entry.kind or surface.ApprovalKind.CALENDAR)
                    context = reminder.ReminderContext(
                        config=reminder_config,
                        journal=calendar_approval._lease_module().ReminderJournal(
                            store.path.parent / "reminder-journal"
                        ),
                        request_type=kind,
                        deliver=lambda _channel_id, content: discord.send_owner_dm(content),
                        clock=lambda: now,
                    )
                    calendar_approval.lifecycle().remind_owner_approval(
                        request, decision, lease, context
                    )
                verdict = calendar_approval.lifecycle().resolve_owner_decision(
                    request, decision, lease,
                )
                outcome = calendar_approval.lifecycle().WatchOutcome
                match verdict.outcome:
                    case outcome.CONSUMED:
                        pass
                    case outcome.WAITING | outcome.SKIPPED:
                        retained.append(entry)
                    case unreachable:
                        assert_never(unreachable)
        except ConfirmWatchError as error:
            _log_failure(error, entry)
            if failures is not None:
                failures.append(error)
            retained.append(entry)
        except Exception as error:  # noqa: BLE001 — per-entry cron failure boundary.
            wrapped = ConfirmWatchError(
                "pending confirmation operation raised",
                stage="entry.process",
                fatal=True,
                stderr=str(error),
                cause_type=type(error).__name__,
            )
            _log_failure(wrapped, entry)
            if failures is not None:
                failures.append(wrapped)
            retained.append(entry)
    return tuple(retained)


def _has_thread(record: Mapping[str, object]) -> bool:
    """A request thread to answer in — this request's own, else the instructing channel."""
    return bool(record.get("approval_thread_id") or record.get("origin_channel_id"))


def _notify_result(
    discord: DiscordClient, record: Mapping[str, object], content: str, outcome: str = ""
) -> None:
    """Send one result notice to this request's thread, else to cha.

    소유자 결정 2026-09-01: 결과는 승인이 게시된 그 스레드로 돌아가 종결 표시된다.
    승인 스레드도 origin 도 없는 옛 초안은 종전 경로(소유자 통지)를 그대로 쓴다.
    """
    if _has_thread(record):
        _notify_thread(record, content, outcome)
        return
    _notify_owner(discord, content)


def _notify_thread(record: Mapping[str, object], content: str, outcome: str = "") -> None:
    """Post to the request thread; the command already committed, so failures only log."""
    try:
        calendar_confirm.notify_result(dict(record), content, outcome)
    except Exception as error:  # noqa: BLE001 — 통지 실패가 완료된 tick을 되돌리면 안 된다
        print(
            f"calendar-confirm-watch thread notification failed: {_redact(str(error))}",
            file=sys.stderr,
        )


def _notify_owner(discord: DiscordClient, content: str) -> None:
    """Send a post-action owner notification, swallowing errors so a notification
    failure cannot re-retain an already-completed (discarded/confirmed) entry.

    The command (discard/confirm) has already succeeded at this point; the
    pending-confirm JSONL entry will be purged by ``remove_completed``.  A
    transient Discord outage must not cause the entry to be retained and
    re-evaluated on every subsequent tick.
    """
    try:
        discord.send_owner_dm(content)
    except ConfirmWatchError as error:
        print(f"calendar-confirm-watch owner notification failed: {_redact(str(error))}", file=sys.stderr)


def reaction_action(entry: PendingConfirm, owner_id: str, discord: DiscordClient) -> str:
    cancel = _owner_reacted(discord.reaction_users(entry, CANCEL_EMOJI), owner_id)
    approve = _owner_reacted(discord.reaction_users(entry, APPROVE_EMOJI), owner_id)
    if cancel:
        return CANCEL_EMOJI
    if approve:
        return APPROVE_EMOJI
    return ""


def _owner_reacted(users: tuple[Mapping[str, str | bool], ...], owner_id: str) -> bool:
    return any(user.get("id", "") == owner_id and not bool(user.get("bot", False)) for user in users)


def _draft_sha256(draft_id: str) -> str:
    try:
        draft = calendar_gate.load_draft(draft_id)
    except calendar_gate.GateError as error:
        raise ConfirmWatchError("pending draft is unavailable") from error
    sha256 = draft.get("sha256")
    if not isinstance(sha256, str):
        raise ConfirmWatchError("pending draft hash is invalid")
    return sha256


def main() -> int:
    try:
        owner = calendar_confirm.owner_id()
        scripts = skill_scripts("calendar", env_var="CALENDAR_SCRIPTS")
        config = calendar_approval._repo_module(
            "approval_reminder_config"
        ).load_approval_reminder_config()
        run_once(
            store=PendingConfirmStore(), owner_id=owner, discord=DiscordApi(owner),
            commands=CliCommands(scripts / "calendar_cli.py"), draft_sha256=_draft_sha256,
            now=datetime.now(UTC), reminder_config=config,
        )
    except ConfirmBatchError as error:
        return error.exit_code
    except Exception as error:  # noqa: BLE001,BROAD_EXCEPT_OK — final cron alert boundary.
        _log_failure(
            ConfirmWatchError(
                "unhandled watcher failure", stage="watcher.main", fatal=True,
                cause_type=type(error).__name__, stderr=str(error),
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
