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

_LIVE_SCRIPTS: Final = "/srv/autophagy-skills/live/calendar/scripts"
_ENV_SECRETS: Final = Path.home() / ".env.secrets"


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

_SCRIPTS = Path(os.environ.get("CALENDAR_SCRIPTS", _LIVE_SCRIPTS)).expanduser()
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


@dataclass(frozen=True, slots=True)
class OwnerDecision:
    entry: PendingConfirm
    owner_id: str
    discord: DiscordClient
    commands: CommandRunner
    draft_sha256: Callable[[str], str]
    store: PendingConfirmStore

    def probe(self, _request):
        if self.draft_sha256(self.entry.draft_id) != self.entry.sha256:
            raise ConfirmWatchError("draft hash mismatch")
        return calendar_approval.probe_entry(self.entry, self.owner_id, self.discord)

    def apply(self, _request, decision) -> None:
        state = calendar_approval.lifecycle().Probe
        match decision:
            case state.APPROVED:
                self.commands.confirm(self.entry, self.owner_id)
            case state.CANCELLED:
                self.commands.discard(self.entry.draft_id)
                _notify_owner(self.discord, "취소됨")
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
) -> None:
    """Apply only unambiguous bound owner reactions and retain uncertain entries."""
    snapshot = store.load()
    failures: list[ConfirmWatchError] = []
    _process_entries(
        snapshot, store, owner_id, discord, commands, draft_sha256, now,
        reminder_config=reminder_config, failures=failures
    )
    fatal_failures = tuple(error for error in failures if error.fatal)
    if fatal_failures:
        raise ConfirmBatchError(fatal_failures)


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
) -> tuple[PendingConfirm, ...]:
    retained: list[PendingConfirm] = []
    for entry in entries:
        try:
            decision = OwnerDecision(entry, owner_id, discord, commands, draft_sha256, store)
            if now.astimezone(UTC) - entry.created > EXPIRY:
                with calendar_approval.confirm_lease(store.path.parent).hold(entry.key) as owned:
                    if not owned:
                        retained.append(entry)
                        continue
                    decision.probe(calendar_approval.request_of(entry))
                    commands.discard(entry.draft_id)
                    _notify_owner(discord, "확정 시간이 지나 취소되었습니다")
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
        scripts = Path(os.environ.get("CALENDAR_SCRIPTS", _SCRIPTS)).expanduser()
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
