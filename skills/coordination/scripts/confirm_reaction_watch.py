#!/usr/bin/env python3
"""Poll cha's owner-only reaction on pending coordination confirmation DMs."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Final, Protocol, assert_never
from urllib.error import HTTPError, URLError
from urllib.parse import quote

_LIVE_SCRIPTS: Final = "/srv/autophagy-skills/live/coordination/scripts"
_SCRIPTS = Path(os.environ.get("COORDINATION_SCRIPTS", _LIVE_SCRIPTS)).expanduser()
if _SCRIPTS.exists() and str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import coordinate_io as io  # noqa: E402 — deployed cron imports the mounted skill scripts.
from coordination_pending import PendingConfirm, PendingConfirmStore  # noqa: E402
coordination_approval = import_module("coordination_approval")

APPROVE_EMOJI = "\u2705"
CANCEL_EMOJI = "\u26d4"
EXPIRY = timedelta(hours=24)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\d{5,}")


class ConfirmWatchError(RuntimeError):
    """A pending confirmation could not be safely evaluated or applied."""


class DiscordClient(Protocol):
    def message_content(self, entry: PendingConfirm) -> str | None: ...

    def reaction_users(self, entry: PendingConfirm, emoji: str) -> tuple[Mapping[str, str | bool], ...]: ...

    def send_owner_dm(self, content: str) -> None: ...


class CommandRunner(Protocol):
    def finalize(self, entry: PendingConfirm) -> None: ...

    def discard(self, draft_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class DiscordApi:
    owner_id: str

    def message_content(self, entry: PendingConfirm) -> str | None:
        """Read the exact confirmation DM so its draft hash remains bound."""
        try:
            message = io.api("GET", f"/channels/{entry.dm_channel_id}/messages/{entry.dm_message_id}")
        except HTTPError as error:
            if error.code == 404:
                return None
            raise ConfirmWatchError("confirmation DM read failed") from error
        except (URLError, OSError) as error:
            raise ConfirmWatchError("confirmation DM read failed") from error
        if not isinstance(message, dict):
            raise ConfirmWatchError("confirmation DM response is invalid")
        content = message.get("content")
        if not isinstance(content, str):
            raise ConfirmWatchError("confirmation DM content is invalid")
        return content

    def reaction_users(self, entry: PendingConfirm, emoji: str) -> tuple[Mapping[str, str | bool], ...]:
        """Mirror skill_gate: a missing reaction endpoint means an empty reaction set."""
        endpoint = (
            f"/channels/{entry.dm_channel_id}/messages/{entry.dm_message_id}"
            f"/reactions/{quote(emoji, safe='')}?limit=100"
        )
        try:
            users = io.api("GET", endpoint)
        except HTTPError as error:
            if error.code == 404:
                return ()
            raise ConfirmWatchError("reaction query failed") from error
        except (URLError, OSError) as error:
            raise ConfirmWatchError("reaction query failed") from error
        if not isinstance(users, list):
            raise ConfirmWatchError("reaction response is invalid")
        return tuple(user for user in users if isinstance(user, dict))

    def send_owner_dm(self, content: str) -> None:
        try:
            io.post_message(io.owner_approval_channel(self.owner_id), content)
        except (HTTPError, URLError, OSError) as error:
            raise ConfirmWatchError("owner notification failed") from error


@dataclass(frozen=True, slots=True)
class CliCommands:
    coordination_cli: Path
    calendar_cli: Path

    def finalize(self, entry: PendingConfirm) -> None:
        try:
            environment = dict(os.environ)
            environment["DISCORD_BOT_TOKEN"] = io.discord_bot_token()
        except io.CoordinationError as error:
            raise ConfirmWatchError("confirmation credential unavailable") from error
        self._run(
            self.coordination_cli,
            "finalize", "--draft", entry.draft_id, "--slot", entry.slot,
            "--summary", entry.summary, "--duration-min", str(entry.duration_min),
            "--correlation", entry.correlation, environment=environment,
        )

    def discard(self, draft_id: str) -> None:
        self._run(self.calendar_cli, "discard", "--draft", draft_id)

    @staticmethod
    def _run(
        script: Path, *arguments: str, environment: Mapping[str, str] | None = None
    ) -> None:
        try:
            result = subprocess.run(  # noqa: S603 — fixed scripts and controlled pending fields.
                [sys.executable, str(script), *arguments], capture_output=True, text=True,
                timeout=180, check=False, cwd=str(Path.home()), env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ConfirmWatchError("confirmation command failed") from error
        if result.returncode != 0:
            raise ConfirmWatchError(f"confirmation command rejected rc={result.returncode}")


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
        return coordination_approval.probe_entry(self.entry, self.owner_id, self.discord)

    def apply(self, _request, decision) -> None:
        state = coordination_approval.lifecycle().Probe
        match decision:
            case state.APPROVED:
                self.commands.finalize(self.entry)
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
) -> None:
    """Apply unambiguous owner reactions and retain every uncertain entry."""
    snapshot = store.load()
    _process_entries(snapshot, store, owner_id, discord, commands, draft_sha256, now)


def _process_entries(
    entries: tuple[PendingConfirm, ...],
    store: PendingConfirmStore,
    owner_id: str,
    discord: DiscordClient,
    commands: CommandRunner,
    draft_sha256: Callable[[str], str],
    now: datetime,
) -> tuple[PendingConfirm, ...]:
    retained: list[PendingConfirm] = []
    for entry in entries:
        try:
            decision = OwnerDecision(entry, owner_id, discord, commands, draft_sha256, store)
            if now.astimezone(UTC) - entry.created > EXPIRY:
                with coordination_approval.confirm_lease(store.path.parent).hold(entry.key) as owned:
                    if not owned:
                        retained.append(entry)
                        continue
                    decision.probe(coordination_approval.request_of(entry))
                    commands.discard(entry.draft_id)
                    _notify_owner(discord, "확정 시간이 지나 취소되었습니다")
                    decision.drop(coordination_approval.request_of(entry))
            else:
                verdict = coordination_approval.lifecycle().resolve_owner_decision(
                    coordination_approval.request_of(entry),
                    decision,
                    coordination_approval.confirm_lease(store.path.parent),
                )
                outcome = coordination_approval.lifecycle().WatchOutcome
                match verdict.outcome:
                    case outcome.CONSUMED:
                        pass
                    case outcome.WAITING | outcome.SKIPPED:
                        retained.append(entry)
                    case unreachable:
                        assert_never(unreachable)
        except ConfirmWatchError as error:
            print(f"coordination-confirm-watch error: {_redact(str(error))}", file=sys.stderr)
            retained.append(entry)
    return tuple(retained)


def _notify_owner(discord: DiscordClient, content: str) -> None:
    """Best-effort owner notification — a completed discard must never be retained."""
    try:
        discord.send_owner_dm(content)
    except ConfirmWatchError as error:
        print(f"coordination-confirm-watch notify failed: {_redact(str(error))}", file=sys.stderr)


def reaction_action(entry: PendingConfirm, owner_id: str, discord: DiscordClient) -> str:
    cancel = _owner_reacted(discord.reaction_users(entry, CANCEL_EMOJI), owner_id)
    approve = _owner_reacted(discord.reaction_users(entry, APPROVE_EMOJI), owner_id)
    if cancel:
        return CANCEL_EMOJI
    if approve:
        return APPROVE_EMOJI
    return ""


def _owner_reacted(users: tuple[Mapping[str, str | bool], ...], owner_id: str) -> bool:
    for user in users:
        user_id = user.get("id", "")
        is_bot = user.get("bot", False)
        if user_id == owner_id and not bool(is_bot):
            return True
    return False


def _draft_sha256(draft_id: str) -> str:
    io.calendar_scripts()
    import calendar_gate

    try:
        draft = calendar_gate.load_draft(draft_id)
    except calendar_gate.GateError as error:
        raise ConfirmWatchError("pending draft is unavailable") from error
    sha256 = draft.get("sha256")
    if not isinstance(sha256, str):
        raise ConfirmWatchError("pending draft hash is invalid")
    return sha256


def _redact(text: str) -> str:
    return _LONG_DIGITS.sub("[MASKED-NUM]", _EMAIL.sub("[MASKED-EMAIL]", text))[:300]


def main() -> int:
    try:
        config = io.interop_config()
        scripts = Path(os.environ.get("COORDINATION_SCRIPTS", _SCRIPTS)).expanduser()
        calendar_scripts = io.calendar_scripts()
        run_once(
            store=PendingConfirmStore(), owner_id=config["owner_id"], discord=DiscordApi(config["owner_id"]),
            commands=CliCommands(scripts / "coordinate_cli.py", calendar_scripts / "calendar_cli.py"),
            draft_sha256=_draft_sha256, now=datetime.now(UTC),
        )
    except Exception as error:  # noqa: BLE001,BROAD_EXCEPT_OK — final cron alert boundary.
        print(f"coordination-confirm-watch error: {_redact(str(error))}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
