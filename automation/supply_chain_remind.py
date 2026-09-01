"""Hourly pointers for unanswered skill-deploy approvals in the existing watcher tick."""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol, TypeAlias

from automation.interop.approval_lease import (
    ApprovalLease,
    ReminderJournal,
    ReminderJournalError,
)
from automation.interop.approval_lifecycle import remind_owner_approval
from automation.interop.approval_reminder import ReminderContext, ReminderVerdict
from automation.interop.approval_reminder_config import ApprovalReminderConfig
from automation.interop.approval_surface import ApprovalKind, ApprovalSurfaceError
from automation.interop.approval_types import ApprovalRequest, Probe
from automation.supply_chain_watch import TickResult

DecisionOf = Callable[[str], str]
Deliver = Callable[[str, str], None]
GuildOf = Callable[[str], str]
ChannelOf = Callable[[Mapping[str, str]], str]
Clock = Callable[[], datetime]
ReportError = Callable[[str, str], None]
JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


class JsonLoader(Protocol):
    def __call__(self, raw: str, /) -> JsonValue: ...

_DISCORD_EPOCH_MS: Final = 1_420_070_400_000
_JSON_LOADS: JsonLoader = json.loads
_DECISION_PROBES: Final = {
    "approved": Probe.APPROVED,
    "denied": Probe.CANCELLED,
    "absent": Probe.BOUND_PENDING,
    "missing": Probe.MISSING,
}


class ReminderRecordError(ValueError):
    """A pending record cannot identify its original approval message."""


@dataclass(frozen=True, slots=True)
class _DecisionWatcher:
    decision_of: DecisionOf

    def probe(self, request: ApprovalRequest) -> Probe:
        return _DECISION_PROBES.get(
            self.decision_of(request.message_id), Probe.UNVERIFIABLE
        )

    def apply(self, request: ApprovalRequest, decision: Probe) -> None:
        del request, decision
        raise ReminderRecordError("a reminder watcher cannot apply an owner decision")

    def drop(self, request: ApprovalRequest) -> None:
        del request
        raise ReminderRecordError("a reminder watcher cannot drop an approval record")


@dataclass(frozen=True, slots=True)
class _StoredChannel:
    record: Mapping[str, str]
    channel_of: ChannelOf

    def __call__(self, _request: ApprovalRequest) -> str:
        return self.channel_of(self.record)


def _field(record: Mapping[str, str], name: str, *, required: bool = True) -> str:
    value = record.get(name, "")
    if required and not value:
        raise ReminderRecordError(f"pending record has no {name}")
    return value


def _created_at(message_id: str) -> str:
    if not message_id.isascii() or not message_id.isdigit():
        raise ReminderRecordError("pending record message_id is not a Discord snowflake")
    snowflake = int(message_id)
    if snowflake.bit_length() > 64:
        raise ReminderRecordError("pending record message_id exceeds a Discord snowflake")
    timestamp_ms = (snowflake >> 22) + _DISCORD_EPOCH_MS
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def _request(
    gate_dir: Path, result: TickResult
) -> tuple[ApprovalRequest, Mapping[str, str]]:
    path = gate_dir / "pending" / f"{result.request.record_name}.json"
    try:
        decoded = _JSON_LOADS(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReminderRecordError(str(path)) from error
    if not isinstance(decoded, dict) or any(
        not isinstance(value, str) for value in decoded.values()
    ):
        raise ReminderRecordError(str(path))
    record = {
        key: value
        for key, value in decoded.items()
        if isinstance(value, str)
    }
    message_id = _field(record, "message_id")
    return (
        ApprovalRequest(
            key=result.request.key,
            action_hash=_field(record, "action_hash", required=False),
            message_id=message_id,
            channel_id=_field(record, "channel_id", required=False),
            created_at=_created_at(message_id),
        ),
        record,
    )


def remind_unanswered(
    results: tuple[TickResult, ...],
    gate_dir: Path,
    *,
    decision_of: DecisionOf,
    channel_of: ChannelOf,
    deliver: Deliver,
    guild_of: GuildOf,
    lease: ApprovalLease,
    config: ApprovalReminderConfig,
    clock: Clock,
    on_error: ReportError,
) -> tuple[ReminderVerdict, ...]:
    """Send due pointers only for records this tick proved are still unanswered."""
    verdicts: list[ReminderVerdict] = []
    for result in results:
        if (
            result.request.kind != ApprovalKind.SKILL_DEPLOY.value
            or result.outcome != "retain"
            or result.reason != "unanswered"
        ):
            continue
        try:
            request, record = _request(gate_dir, result)
            context = ReminderContext(
                config=config,
                journal=ReminderJournal(gate_dir / "reminder-journal"),
                request_type=ApprovalKind.SKILL_DEPLOY,
                deliver=deliver,
                clock=clock,
                guild_id_for=guild_of,
                source_channel_id_for=_StoredChannel(record, channel_of),
            )
            verdicts.append(
                remind_owner_approval(
                    request, _DecisionWatcher(decision_of), lease, context
                )
            )
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            ApprovalSurfaceError,
            ReminderJournalError,
        ) as error:
            on_error(result.request.key, type(error).__name__)
    return tuple(verdicts)
