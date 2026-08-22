"""Regression tests for the public Discord message-visibility policy (t_2eb92533).

The bug these guard (t_db6a60e8): the gateway rendered its own internal work
process onto Discord guild channels and threads — "Reading skill …", file
searches, terminal commands, raw tool-call arguments, thinking text and raw
background-process output. The fix is an event-kind allowlist, so the tests are
written the same way: no sleeps, no clocks, no I/O — a decision table and a
delivery simulation over it.
"""

from __future__ import annotations

from dataclasses import dataclass

from automation.hermes_compat.public_message_policy import (
    DISPLAY_SURFACE_KINDS,
    PUBLIC_DISCORD_ALLOWLIST,
    WITHHELD_COMMAND,
    WITHHELD_DESCRIPTION,
    PublicMessageKind,
    allows_public_discord_event,
    audit_suppressed_event,
    decide,
    display_surface_allowed,
    display_surface_kind,
    event_allowed_on_surface,
    is_public_discord_surface,
    normalize_kind,
    public_background_completion_text,
)


@dataclass(frozen=True, slots=True)
class _Source:
    """Stand-in for the gateway's SessionSource (only the read fields)."""

    platform: str = "discord"
    chat_type: str = "channel"
    chat_id: str = "1490000000000000001"
    thread_id: str = "1490000000000000002"


class _Platform:
    """Stand-in for the gateway's Platform enum (policy reads ``.value``)."""

    def __init__(self, value: str) -> None:
        self.value = value


class _RecordingLogger:
    """Captures ``logger.info`` calls without formatting them."""

    def __init__(self) -> None:
        self.records: list[tuple[str, tuple[object, ...]]] = []

    def info(self, msg: str, /, *args: object) -> None:
        self.records.append((msg, args))

    def rendered(self) -> list[str]:
        return [msg % args for msg, args in self.records]


# Payloads observed leaking into public Discord surfaces, with the event kind
# that carried them. Every one of these must be denied off a DM.
LEAKING_PAYLOADS: tuple[tuple[PublicMessageKind, str], ...] = (
    (PublicMessageKind.TOOL_PROGRESS, "Reading skill: recall/SKILL.md"),
    (PublicMessageKind.TOOL_PROGRESS, "\N{HAMMER AND WRENCH} grep -rn 'api_key' /home/agent/.hermes"),
    (PublicMessageKind.TOOL_PROGRESS, 'terminal(command="rm -rf /srv/autophagy-skills/live/mail")'),
    (PublicMessageKind.TOOL_PROGRESS, '{"tool": "file_search", "args": {"pattern": "**/*.env"}}'),
    (PublicMessageKind.INTERIM_ASSISTANT, "Let me check the config file first…"),
    (PublicMessageKind.REASONING, "The user probably means the peer node, so I should…"),
    (PublicMessageKind.STREAMING_DRAFT, "Looking at gateway/ru"),
    (PublicMessageKind.INTERNAL_STATUS, "Context pressure high — compacting conversation"),
    (PublicMessageKind.LONG_RUNNING_STATUS, "Working… (3m elapsed)"),
    (PublicMessageKind.BACKGROUND_PROGRESS, "+ pytest tests/unit\n.....F"),
    (PublicMessageKind.BACKGROUND_OUTPUT, "[Background process bg_7 finished… Here's the final output:\nsecret]"),
)

ALLOWED_PAYLOADS: tuple[tuple[PublicMessageKind, str], ...] = (
    (PublicMessageKind.FINAL_RESULT, "배포를 마쳤습니다."),
    (PublicMessageKind.APPROVAL_REQUEST, "A protected operation requires your approval."),
    (PublicMessageKind.USER_QUESTION, "어느 노드에 배포할까요?"),
    (PublicMessageKind.FAILURE_SUMMARY, "\N{CROSS MARK} 작업이 실패했습니다."),
    (PublicMessageKind.COMPLETION_NOTIFICATION, "\N{WHITE HEAVY CHECK MARK} 완료했습니다."),
)


def _deliver(surface_chat_type: str, events: tuple[tuple[PublicMessageKind, str], ...]) -> list[str]:
    """Simulate the injected seams: render only what the policy allows."""
    return [
        text
        for kind, text in events
        if event_allowed_on_surface(_Platform("discord"), surface_chat_type, kind)
    ]


# --------------------------------------------------------------------------
# Surface classification
# --------------------------------------------------------------------------


def test_discord_dm_is_the_only_private_discord_surface() -> None:
    assert not is_public_discord_surface(_Platform("discord"), "dm")
    for chat_type in ("channel", "thread", "auto_thread", "group", "forum_post"):
        assert is_public_discord_surface(_Platform("discord"), chat_type), chat_type


def test_unknown_or_missing_discord_chat_type_fails_closed_to_public() -> None:
    for chat_type in (None, "", "   ", "something_upstream_added_later"):
        assert is_public_discord_surface(_Platform("discord"), chat_type)


def test_dm_detection_is_case_and_whitespace_insensitive() -> None:
    assert not is_public_discord_surface(_Platform("DISCORD"), " DM ")


def test_non_discord_platforms_are_untouched() -> None:
    for platform in ("slack", "telegram", "matrix", "webhook", "local", None):
        assert not is_public_discord_surface(_Platform(str(platform)), "channel")
        assert event_allowed_on_surface(
            _Platform(str(platform)), "channel", PublicMessageKind.TOOL_PROGRESS
        )


# --------------------------------------------------------------------------
# t_2eb92533 core: leak payloads never reach a public payload list
# --------------------------------------------------------------------------


def test_leaking_internal_payloads_are_blocked_on_public_discord() -> None:
    delivered = _deliver("channel", LEAKING_PAYLOADS)
    assert delivered == []


def test_leaking_internal_payloads_are_blocked_in_public_threads_too() -> None:
    # A thread is not a privacy boundary: its ACLs live in Discord, not Hermes.
    assert _deliver("thread", LEAKING_PAYLOADS) == []
    assert _deliver("auto_thread", LEAKING_PAYLOADS) == []


def test_every_leak_marker_string_is_absent_from_the_public_transcript() -> None:
    transcript = "\n".join(_deliver("channel", LEAKING_PAYLOADS + ALLOWED_PAYLOADS))
    for marker in (
        "Reading skill",
        "grep -rn",
        "rm -rf",
        "file_search",
        "**/*.env",
        "Let me check",
        "Context pressure",
        "Working…",
        "pytest tests/unit",
        "final output",
    ):
        assert marker not in transcript, marker


def test_owner_dm_still_receives_everything() -> None:
    # Suppression is surface-scoped; the private operational view is unchanged.
    assert _deliver("dm", LEAKING_PAYLOADS) == [text for _, text in LEAKING_PAYLOADS]


def test_allowed_events_pass_through_on_public_discord() -> None:
    assert _deliver("channel", ALLOWED_PAYLOADS) == [text for _, text in ALLOWED_PAYLOADS]


# --------------------------------------------------------------------------
# Allowlist shape and unknown-kind default
# --------------------------------------------------------------------------


def test_allowlist_is_exactly_the_five_user_facing_kinds() -> None:
    assert PUBLIC_DISCORD_ALLOWLIST == frozenset(
        {
            PublicMessageKind.FINAL_RESULT,
            PublicMessageKind.APPROVAL_REQUEST,
            PublicMessageKind.USER_QUESTION,
            PublicMessageKind.FAILURE_SUMMARY,
            PublicMessageKind.COMPLETION_NOTIFICATION,
        }
    )


def test_unknown_event_kinds_default_to_private() -> None:
    for kind in ("some_new_upstream_event", "", None, 17, object(), PublicMessageKind.UNKNOWN):
        assert not allows_public_discord_event(kind), kind
        assert not event_allowed_on_surface(_Platform("discord"), "channel", kind), kind


def test_unknown_kinds_normalize_rather_than_raise() -> None:
    assert normalize_kind("definitely_not_a_kind") is PublicMessageKind.UNKNOWN
    assert normalize_kind(None) is PublicMessageKind.UNKNOWN
    assert normalize_kind(PublicMessageKind.FINAL_RESULT) is PublicMessageKind.FINAL_RESULT
    assert normalize_kind("final_result") is PublicMessageKind.FINAL_RESULT


def test_no_internal_telemetry_kind_is_allowlisted() -> None:
    for kind in (
        PublicMessageKind.STREAMING_DRAFT,
        PublicMessageKind.TOOL_PROGRESS,
        PublicMessageKind.INTERIM_ASSISTANT,
        PublicMessageKind.REASONING,
        PublicMessageKind.INTERNAL_STATUS,
        PublicMessageKind.LONG_RUNNING_STATUS,
        PublicMessageKind.BACKGROUND_PROGRESS,
        PublicMessageKind.BACKGROUND_OUTPUT,
        PublicMessageKind.UNKNOWN,
    ):
        assert kind not in PUBLIC_DISCORD_ALLOWLIST, kind


def test_decision_records_why_not_just_the_verdict() -> None:
    verdict = decide(_Platform("discord"), "thread", "tool_progress")
    assert verdict.kind is PublicMessageKind.TOOL_PROGRESS
    assert verdict.public_surface is True
    assert verdict.allowed is False

    dm_verdict = decide(_Platform("discord"), "dm", "tool_progress")
    assert dm_verdict.public_surface is False
    assert dm_verdict.allowed is True


# --------------------------------------------------------------------------
# Display-surface settings (the seam the patch injects into the resolver)
# --------------------------------------------------------------------------


def test_known_display_surfaces_map_to_denied_kinds() -> None:
    for setting in ("interim_assistant_messages", "thinking_progress", "long_running_notifications"):
        assert display_surface_kind(setting) is not PublicMessageKind.UNKNOWN, setting
        assert not display_surface_allowed(_Platform("discord"), "channel", setting), setting
        assert display_surface_allowed(_Platform("discord"), "dm", setting), setting


def test_display_surface_added_upstream_later_fails_closed() -> None:
    assert "some_future_relay" not in DISPLAY_SURFACE_KINDS
    assert display_surface_kind("some_future_relay") is PublicMessageKind.UNKNOWN
    assert not display_surface_allowed(_Platform("discord"), "channel", "some_future_relay")


# --------------------------------------------------------------------------
# Completion notice carries no payload
# --------------------------------------------------------------------------


def test_background_completion_text_is_opaque() -> None:
    text = public_background_completion_text("bg_7f21", 0)
    assert "bg_7f21" in text
    for leak in ("rm -rf", "pytest", "/home/agent", "output"):
        assert leak not in text, leak


def test_background_completion_text_distinguishes_outcomes() -> None:
    assert "completed successfully" in public_background_completion_text("bg_1", 0)
    assert "completed successfully" in public_background_completion_text("bg_1", "0")
    assert "failed (exit code 2)" in public_background_completion_text("bg_1", 2)
    assert "completed." in public_background_completion_text("bg_1", None)


def test_background_completion_label_is_bounded_and_never_empty() -> None:
    assert "background task" in public_background_completion_text("", 0)
    assert "background task" in public_background_completion_text(None, 0)
    long_label = public_background_completion_text("x" * 500, 1)
    assert "x" * 81 not in long_label


def test_withheld_approval_text_carries_no_command() -> None:
    assert "withheld" in WITHHELD_COMMAND
    assert "approval" in WITHHELD_DESCRIPTION
    for leak in ("sudo", "rm ", "curl", "ssh "):
        assert leak not in WITHHELD_COMMAND, leak
        assert leak not in WITHHELD_DESCRIPTION, leak


# --------------------------------------------------------------------------
# Private audit record survives the suppression
# --------------------------------------------------------------------------


def test_suppression_is_recorded_in_the_private_log() -> None:
    logger = _RecordingLogger()
    source = _Source(chat_type="thread", chat_id="149001", thread_id="149002")
    audit_suppressed_event(logger, PublicMessageKind.TOOL_PROGRESS, source)
    assert len(logger.records) == 1
    line = logger.rendered()[0]
    assert "suppressed" in line
    assert "tool_progress" in line
    assert "149001" in line
    assert "149002" in line


def test_audit_record_is_content_free() -> None:
    logger = _RecordingLogger()
    audit_suppressed_event(logger, PublicMessageKind.TOOL_PROGRESS, _Source())
    line = logger.rendered()[0]
    for payload in ("Reading skill", "grep", "rm -rf", "file_search"):
        assert payload not in line, payload


def test_unknown_kind_is_audited_as_unknown_not_dropped() -> None:
    logger = _RecordingLogger()
    audit_suppressed_event(logger, "some_new_upstream_event", _Source())
    assert "unknown" in logger.rendered()[0]


def test_audit_tolerates_a_source_without_routing_fields() -> None:
    logger = _RecordingLogger()
    audit_suppressed_event(logger, PublicMessageKind.REASONING, None)
    assert "reasoning" in logger.rendered()[0]


def test_audit_bounds_untrusted_identifier_lengths() -> None:
    logger = _RecordingLogger()
    audit_suppressed_event(logger, PublicMessageKind.REASONING, _Source(chat_id="9" * 400))
    assert "9" * 97 not in logger.rendered()[0]
