"""Fail-closed visibility policy for public Discord surfaces of the gateway.

WHY: the vendored Hermes gateway renders its own turn telemetry — token
streaming drafts, tool-progress lines with raw arguments, thinking/reasoning
relay, context-pressure status callbacks, "still working" heartbeats and raw
background-process output — onto whatever surface the turn came from. In an
1:1 Discord DM that is a private operational view; in a guild channel or thread it is
the agent's internal work process on a user-facing surface, which is what
t_db6a60e8 asks us to stop.

The policy is **event-typed, not string-scrubbing**: callers classify an event
before rendering it instead of pattern-matching leaked text after the fact. A
scrubber only ever removes the leaks someone already thought of; an allowlist
denies the ones nobody has seen yet. Unknown event kinds and unknown Discord
chat types therefore both deny.

Suppression changes delivery only. The session database, the process registry,
and the redacted gateway log remain the private operational record — the
``audit_*`` helper here writes a content-free decision line into that same
existing log rather than copying payload text anywhere new.

Deployed to ``~/.hermes/hermes-compat/automation/hermes_compat/`` as a runtime
dependency of the ``discord-public-message-policy`` patch; the injected gateway
code imports it through ``hermes_compat_boot``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final, Protocol

_WITHHELD_MAX_LABEL: Final = 80


class PublicMessageKind(str, Enum):
    """Every gateway-originated event kind the policy can be asked about.

    Membership in this enum is not permission — :data:`PUBLIC_DISCORD_ALLOWLIST`
    is. Kinds are listed here so a suppression decision can be *named* in the
    audit log instead of logging an opaque boolean.
    """

    # User-facing: the answer, or a question the user must act on.
    FINAL_RESULT = "final_result"
    APPROVAL_REQUEST = "approval_request"
    USER_QUESTION = "user_question"
    FAILURE_SUMMARY = "failure_summary"
    COMPLETION_NOTIFICATION = "completion_notification"

    # Internal turn telemetry.
    STREAMING_DRAFT = "streaming_draft"
    TOOL_PROGRESS = "tool_progress"
    INTERIM_ASSISTANT = "interim_assistant"
    REASONING = "reasoning"
    INTERNAL_STATUS = "internal_status"
    LONG_RUNNING_STATUS = "long_running_status"
    BACKGROUND_PROGRESS = "background_progress"
    BACKGROUND_OUTPUT = "background_output"

    # Anything we could not name. Never allowed.
    UNKNOWN = "unknown"


PUBLIC_DISCORD_ALLOWLIST: Final[frozenset[PublicMessageKind]] = frozenset(
    {
        PublicMessageKind.FINAL_RESULT,
        PublicMessageKind.APPROVAL_REQUEST,
        PublicMessageKind.USER_QUESTION,
        PublicMessageKind.FAILURE_SUMMARY,
        PublicMessageKind.COMPLETION_NOTIFICATION,
    }
)

# Gateway display-surface setting names (`display.platforms.<plat>.<setting>`)
# mapped onto the event kind they render. The injected patch consults this so a
# *setting* the gateway resolves can be judged by the same allowlist; a setting
# upstream adds later is not in the map, resolves to UNKNOWN, and is denied.
DISPLAY_SURFACE_KINDS: Final[Mapping[str, PublicMessageKind]] = MappingProxyType(
    {
        "interim_assistant_messages": PublicMessageKind.INTERIM_ASSISTANT,
        "thinking_progress": PublicMessageKind.REASONING,
        "long_running_notifications": PublicMessageKind.LONG_RUNNING_STATUS,
        "tool_progress": PublicMessageKind.TOOL_PROGRESS,
        "streaming": PublicMessageKind.STREAMING_DRAFT,
        "live_status": PublicMessageKind.INTERNAL_STATUS,
    }
)

WITHHELD_COMMAND: Final = "[operation details withheld on public Discord]"
WITHHELD_DESCRIPTION: Final = "A protected operation requires your approval."


class AuditLogger(Protocol):
    """The subset of ``logging.Logger`` the audit helper needs."""

    def info(self, msg: str, /, *args: object) -> None: ...


@dataclass(frozen=True, slots=True)
class SurfaceDecision:
    """A single, explainable visibility verdict."""

    kind: PublicMessageKind
    public_surface: bool
    allowed: bool


def normalize_kind(kind: PublicMessageKind | str | None) -> PublicMessageKind:
    """Coerce anything to a known kind; malformed or novel values become UNKNOWN."""
    if isinstance(kind, PublicMessageKind):
        return kind
    try:
        return PublicMessageKind(kind)
    except (TypeError, ValueError):
        return PublicMessageKind.UNKNOWN


def display_surface_kind(setting: object) -> PublicMessageKind:
    """Map a gateway display-surface setting name onto its event kind."""
    return DISPLAY_SURFACE_KINDS.get(str(setting or "").strip(), PublicMessageKind.UNKNOWN)


def _platform_name(platform: object) -> str:
    return str(getattr(platform, "value", platform) or "").strip().lower()


def is_public_discord_surface(platform: object, chat_type: object) -> bool:
    """Return whether a route is a Discord surface outside a 1:1 DM.

    Only ``chat_type == "dm"`` is private. A guild channel, an auto-created
    thread, an existing thread, and any chat type Hermes grows later are all
    public — a thread is not a privacy boundary because its ACLs live in
    Discord, outside anything ``SessionSource`` can prove.
    """
    if _platform_name(platform) != "discord":
        return False
    return str(chat_type or "").strip().lower() != "dm"


def allows_public_discord_event(kind: PublicMessageKind | str | None) -> bool:
    """Return allowlist membership; malformed and new kinds are denied."""
    return normalize_kind(kind) in PUBLIC_DISCORD_ALLOWLIST


def decide(
    platform: object,
    chat_type: object,
    kind: PublicMessageKind | str | None,
) -> SurfaceDecision:
    """Judge one event on one route, keeping the reasoning inspectable."""
    normalized = normalize_kind(kind)
    public = is_public_discord_surface(platform, chat_type)
    return SurfaceDecision(
        kind=normalized,
        public_surface=public,
        allowed=(not public) or normalized in PUBLIC_DISCORD_ALLOWLIST,
    )


def event_allowed_on_surface(
    platform: object,
    chat_type: object,
    kind: PublicMessageKind | str | None,
) -> bool:
    """Apply the policy, leaving DMs and non-Discord platforms untouched."""
    return decide(platform, chat_type, kind).allowed


def display_surface_allowed(platform: object, chat_type: object, setting: object) -> bool:
    """Judge a gateway display-surface setting by the event kind it renders."""
    return event_allowed_on_surface(platform, chat_type, display_surface_kind(setting))


def public_background_completion_text(session_id: object, exit_code: object) -> str:
    """Build a completion notice with no command, output, path, or tool args."""
    label = str(session_id or "background task").strip() or "background task"
    # Session IDs are opaque generated handles, but bound the length anyway in
    # case a custom process registry hands us something unexpected.
    label = label[:_WITHHELD_MAX_LABEL]
    if exit_code in {0, "0"}:
        return f"\N{WHITE HEAVY CHECK MARK} Background task `{label}` completed successfully."
    if exit_code is None:
        return f"\N{INFORMATION SOURCE}\N{VARIATION SELECTOR-16} Background task `{label}` completed."
    return f"\N{WARNING SIGN}\N{VARIATION SELECTOR-16} Background task `{label}` failed (exit code {exit_code})."


def audit_suppressed_event(
    logger: AuditLogger,
    kind: PublicMessageKind | str | None,
    source: object,
) -> None:
    """Record a content-free suppression decision in the private gateway log."""
    logger.info(
        "Public Discord delivery suppressed: event=%s chat_type=%s chat_id=%s thread_id=%s",
        normalize_kind(kind).value,
        str(getattr(source, "chat_type", "") or "")[:24],
        str(getattr(source, "chat_id", "") or "")[:96],
        str(getattr(source, "thread_id", "") or "")[:96],
    )
