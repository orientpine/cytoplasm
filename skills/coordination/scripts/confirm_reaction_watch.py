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


# WHY (규약 (b)): this watcher reads Discord reactions in-process and spawns
# `coordination_cli.py`, both of which need credentials, and Hermes no-agent cron puts
# none in os.environ. Measured 2026-08-18 on `budget-watch`: without this step the
# configuration sits on disk and never reaches the code that needs it. Must run before
# the mounted-skill imports below, which already read configuration.
# Inventory check: tests/unit/test_watcher_secret_propagation.py.
def _load_env_secrets(path: Path = _ENV_SECRETS) -> None:
    """Load ``~/.env.secrets`` into ``os.environ`` without overriding the system env."""
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
_SCRIPTS = skill_scripts("coordination", env_var="COORDINATION_SCRIPTS")
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
                timeout=180, check=False, cwd=str(Path.home()),
                # 규약 (b-2): state the child's environment rather than letting it inherit.
                # `env=environment` alone read as explicit but was None on the discard
                # path, i.e. plain inheritance — the fallback the rule exists to forbid.
                env={**os.environ, **(environment or {})},
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
                _notify_result(
                    self.discord, self.entry,
                    f"⛔ 일정 조율 취소 (draft {self.entry.draft_id}) — "
                    "소유자 ⛔ 리액션으로 취소되었습니다.",
                    outcome="cancelled",
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
                    _notify_result(
                        discord, entry,
                        f"⌛ 일정 조율 만료 취소 (draft {entry.draft_id}) — "
                        "확정 시간이 지나 취소되었습니다.",
                        outcome="expired",
                    )
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


def _notify_result(
    discord: DiscordClient, entry: PendingConfirm, content: str, *, outcome: str = ""
) -> None:
    """Best-effort result notice — a completed discard must never be retained.

    라우팅은 CLI와 같은 `coordination_lifecycle.notify_result`가 소유한다(요청별
    승인 스레드 우선, 이 워처의 소유자 표면이 폴백). ``outcome``은 그 스레드를
    종결 표시하는 상태 토큰이다. 임포트는 지연시킨다: lifecycle이 이 모듈을
    임포트하므로 모듈 최상단에서 부르면 순환이 된다. 스레드 게시에 필요한
    자격증명은 모듈 로드 시 `_load_env_secrets()`가 os.environ에 올려둔다
    (규약 (b): cron은 아무것도 넘겨주지 않는다).
    """
    try:
        lifecycle = import_module("coordination_lifecycle")
        lifecycle.notify_result(
            entry.origin_record(), content, fallback=discord.send_owner_dm, outcome=outcome
        )
    except Exception as error:  # noqa: BLE001 — notification must never break the tick
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
        scripts = skill_scripts("coordination", env_var="COORDINATION_SCRIPTS")
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
