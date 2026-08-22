"""Shared scheduling, direct-link, and delivery policy for approval reminders.

This module does not create another approval state machine. Existing approval watchers
observe lifecycle state and use minimum-information links to the original message.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit

from automation.interop.approval_lease import ApprovalLease, ReminderJournal
from automation.interop.approval_reminder_config import ApprovalReminderConfig
from .approval_types import ApprovalRequest, Probe
from automation.interop.approval_surface import ApprovalKind


class ReminderStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    CANCELLED = "cancelled"
    EXECUTED = "executed"
    DISCARDED = "discarded"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    SOURCE_DELETED = "source-deleted"
    UNVERIFIABLE = "unverifiable"


_TERMINAL = frozenset({
    ReminderStatus.APPROVED,
    ReminderStatus.CANCELLED,
    ReminderStatus.EXECUTED,
    ReminderStatus.DISCARDED,
    ReminderStatus.EXPIRED,
    ReminderStatus.SUPERSEDED,
    ReminderStatus.SOURCE_DELETED,
})


ReminderConfig = ApprovalReminderConfig


class ReminderObserver(Protocol):
    def status_for(self, request: ApprovalRequest) -> ReminderStatus: ...


class ReminderSender(Protocol):
    def send(self, request: ApprovalRequest, slot: int, due_at: datetime) -> None: ...


class ReminderOutcome(StrEnum):
    SENT = "sent"
    NOT_DUE = "not-due"
    ALREADY_CLAIMED = "already-claimed"
    RETIRED = "retired"
    DEFERRED = "deferred"
    SKIPPED = "skipped"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class ReminderVerdict:
    outcome: ReminderOutcome
    slot: int | None = None
    due_at: datetime | None = None
    status: ReminderStatus | None = None


def _parse_posted_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("approval created_at must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("approval created_at must include a timezone")
    return parsed.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def due_slot(posted_at: datetime, now: datetime, config: ReminderConfig) -> int | None:
    """Return the latest due slot, anchored to ``posted_at`` (catch-up coalesces)."""
    posted_at, now = posted_at.astimezone(UTC), now.astimezone(UTC)
    first_due = posted_at + config.initial_delay
    if now < first_due:
        return None
    return int((now - first_due) // config.repeat_interval)


def slot_due_at(posted_at: datetime, slot: int, config: ReminderConfig) -> datetime:
    if slot < 0:
        raise ValueError("slot must not be negative")
    return posted_at.astimezone(UTC) + config.initial_delay + slot * config.repeat_interval


def dispatch_due_reminder(
    request: ApprovalRequest,
    observer: ReminderObserver,
    sender: ReminderSender,
    lease: ApprovalLease,
    journal: ReminderJournal,
    config: ReminderConfig,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ReminderVerdict:
    """Observe lifecycle and send at most one reminder for the latest due slot.

    The existing approval-key lease covers observation, durable claim, and send. Thus
    producer/watcher and watcher/watcher races cannot duplicate the same interval.
    """
    if not config.enabled:
        return ReminderVerdict(ReminderOutcome.DISABLED)
    now = clock().astimezone(UTC)
    posted_at = _parse_posted_at(request.created_at)
    with lease.hold(request.key) as owned:
        if not owned:
            return ReminderVerdict(ReminderOutcome.SKIPPED)
        if journal.is_retired(request.key, request.message_id):
            return ReminderVerdict(ReminderOutcome.RETIRED)
        status = observer.status_for(request)
        match status:
            case ReminderStatus.UNVERIFIABLE:
                return ReminderVerdict(ReminderOutcome.DEFERRED, status=status)
            case ReminderStatus.PENDING:
                pass
            case terminal if terminal in _TERMINAL:
                journal.retire(request.key, request.message_id, terminal.value, _timestamp(now))
                return ReminderVerdict(ReminderOutcome.RETIRED, status=terminal)
            case unexpected:
                raise ValueError(f"unknown reminder status: {unexpected!r}")
        slot = due_slot(posted_at, now, config)
        if slot is None:
            return ReminderVerdict(ReminderOutcome.NOT_DUE)
        due_at = slot_due_at(posted_at, slot, config)
        next_due = slot_due_at(posted_at, slot + 1, config)
        if not journal.claim(
            request.key, request.message_id, slot, _timestamp(due_at),
            _timestamp(next_due), _timestamp(now)
        ):
            return ReminderVerdict(ReminderOutcome.ALREADY_CLAIMED, slot=slot, due_at=due_at)
        # The claim deliberately survives every send failure, including SIGKILL.  A
        # transport error may happen after remote delivery, so releasing it would turn
        # an uncertain result into a duplicate.  Later wall-clock slots remain eligible.
        sender.send(request, slot, due_at)
        journal.mark_sent(
            request.key, request.message_id, slot, _timestamp(now), _timestamp(next_due)
        )
        return ReminderVerdict(ReminderOutcome.SENT, slot=slot, due_at=due_at)


class ReminderBoundaryError(ValueError):
    """Reminder metadata cannot be rendered without crossing the boundary."""


class LinkStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"
    DELETED = "deleted"


class SourceLifecycle(StrEnum):
    ACTIVE = "active"
    DELETED = "deleted"


class DeliveryRoute(StrEnum):
    OWNER = "owner"
    ORIGINAL = "original"


@dataclass(frozen=True, slots=True)
class DiscordSource:
    """Content-free coordinates for one Discord message.

    ``channel_id`` may be a guild channel or a thread id.  A missing
    ``guild_id`` means the message is in a DM and therefore uses Discord's
    supported ``@me`` route.
    """

    channel_id: str
    message_id: str
    guild_id: str | None = None


@dataclass(frozen=True, slots=True)
class SourceReference:
    platform: str
    permalink: str | None = None
    discord: DiscordSource | None = None
    lifecycle: SourceLifecycle = SourceLifecycle.ACTIVE


@dataclass(frozen=True, slots=True)
class LinkResult:
    status: LinkStatus
    url: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalReminder:
    request_type: ApprovalKind
    elapsed: timedelta
    source_url: str


@dataclass(frozen=True, slots=True)
class DeliveryScope:
    source_channel_id: str
    owner_dm_channel_id: str


@dataclass(frozen=True, slots=True)
class DeliveryTarget:
    channel_id: str
    route: DeliveryRoute


def _snowflake(value: str | None) -> bool:
    return isinstance(value, str) and bool(value) and value.isascii() and value.isdigit()


def _safe_https_url(value: str) -> bool:
    if not value or any(character in value for character in ("\r", "\n", "\t")):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )


def discord_message_link(source: DiscordSource) -> LinkResult:
    """Build a canonical Discord client link without inspecting message data."""
    identifiers = (source.channel_id, source.message_id)
    if source.guild_id is not None:
        identifiers += (source.guild_id,)
    if not all(_snowflake(identifier) for identifier in identifiers):
        return LinkResult(LinkStatus.INVALID, detail="source coordinates are invalid")
    location = source.guild_id if source.guild_id is not None else "@me"
    return LinkResult(
        LinkStatus.AVAILABLE,
        f"https://discord.com/channels/{location}/{source.channel_id}/{source.message_id}",
    )


def resolve_source_link(source: SourceReference) -> LinkResult:
    """Resolve a direct source link, failing explicitly rather than copying payload."""
    if source.lifecycle is SourceLifecycle.DELETED:
        return LinkResult(LinkStatus.DELETED, detail="original approval message was deleted")
    platform = source.platform.strip().casefold()
    if platform == "discord":
        if source.discord is None:
            return LinkResult(LinkStatus.INVALID, detail="source coordinates are missing")
        return discord_message_link(source.discord)
    if source.permalink is not None:
        if _safe_https_url(source.permalink):
            return LinkResult(LinkStatus.AVAILABLE, source.permalink)
        return LinkResult(LinkStatus.INVALID, detail="source permalink is invalid")
    return LinkResult(
        LinkStatus.UNAVAILABLE,
        detail="this approval surface does not provide a direct link",
    )


def _request_type_text(value: object) -> str:
    if not isinstance(value, ApprovalKind):
        raise ReminderBoundaryError("request type must be a known approval kind")
    return value.value


def _validate_source_url(value: str) -> str:
    if not _safe_https_url(value):
        raise ReminderBoundaryError("source link must be a safe HTTPS permalink")
    return value


def _elapsed_text(elapsed: timedelta) -> str:
    seconds = elapsed.total_seconds()
    if seconds < 0:
        raise ReminderBoundaryError("elapsed time cannot be negative")
    total_minutes = int(seconds // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}시간 {minutes}분"
    if hours:
        return f"{hours}시간"
    return f"{minutes}분"


def compose_reminder(reminder: ApprovalReminder) -> str:
    """Render only request type, elapsed time and the original-message link."""
    request_type = _request_type_text(reminder.request_type)
    source_url = _validate_source_url(reminder.source_url)
    elapsed = _elapsed_text(reminder.elapsed)
    return (
        "승인 리마인더\n"
        f"요청 유형: {request_type}\n"
        f"경과시간: {elapsed}\n"
        f"원문 링크: {source_url}"
    )


def authorize_delivery(scope: DeliveryScope, target: DeliveryTarget) -> bool:
    """Allow only policy-confined endpoints with a matching route and id."""
    if not _snowflake(target.channel_id):
        return False
    owner_route = (
        target.route is DeliveryRoute.OWNER
        and target.channel_id == scope.owner_dm_channel_id
    )
    original_route = (
        target.route is DeliveryRoute.ORIGINAL
        and target.channel_id == scope.source_channel_id
    )
    return owner_route or original_route


class ApprovalProbe(Protocol):
    def probe(self, request: ApprovalRequest) -> Probe: ...


@dataclass(frozen=True, slots=True)
class ReminderContext:
    """Injected watcher runtime; no config, clock, state, or transport is global."""

    config: ApprovalReminderConfig
    journal: ReminderJournal
    request_type: ApprovalKind
    deliver: Callable[[str, str], None]
    clock: Callable[[], datetime]
    guild_id: str | None = None


@dataclass(frozen=True, slots=True)
class _LifecycleObserver:
    watcher: ApprovalProbe

    def status_for(self, request: ApprovalRequest) -> ReminderStatus:
        decision = self.watcher.probe(request)
        return {
            Probe.BOUND_PENDING: ReminderStatus.PENDING,
            Probe.APPROVED: ReminderStatus.APPROVED,
            Probe.CANCELLED: ReminderStatus.CANCELLED,
            Probe.MISSING: ReminderStatus.SOURCE_DELETED,
            Probe.BINDING_MISMATCH: ReminderStatus.UNVERIFIABLE,
            Probe.UNVERIFIABLE: ReminderStatus.UNVERIFIABLE,
        }[decision]


@dataclass(frozen=True, slots=True)
class _PointerSender:
    context: ReminderContext

    def send(self, request: ApprovalRequest, slot: int, due_at: datetime) -> None:
        del slot, due_at
        link = discord_message_link(
            DiscordSource(request.channel_id, request.message_id, self.context.guild_id)
        )
        if link.status is not LinkStatus.AVAILABLE or link.url is None:
            raise ReminderBoundaryError("original approval link is unavailable")
        target = DeliveryTarget(request.channel_id, DeliveryRoute.ORIGINAL)
        scope = DeliveryScope(request.channel_id, request.channel_id)
        if not authorize_delivery(scope, target):
            raise ReminderBoundaryError("reminder target is outside the approval surface")
        elapsed = self.context.clock().astimezone(UTC) - _parse_posted_at(request.created_at)
        body = compose_reminder(
            ApprovalReminder(self.context.request_type, elapsed, link.url)
        )
        self.context.deliver(target.channel_id, body)


def dispatch_owner_approval_reminder(
    request: ApprovalRequest,
    watcher: ApprovalProbe,
    lease: ApprovalLease,
    context: ReminderContext,
) -> ReminderVerdict:
    """Run reminder policy inside an existing approval watcher's tick."""
    return dispatch_due_reminder(
        request,
        _LifecycleObserver(watcher),
        _PointerSender(context),
        lease,
        context.journal,
        context.config,
        clock=context.clock,
    )
