"""Fail-closed approval checks for Hermes external-effect tool calls."""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias

from automation.interop.injection_adapter import InboundEvent, accept_test_event


JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class DenylistConfigurationError(ValueError):
    """Raised when the external-effect denylist cannot be parsed safely."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    """The deterministic tool identity used for approval binding."""

    tool_name: str
    arguments: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ApprovalContext:
    """The environment that determines which approval records are valid."""

    approval_log: Path | None
    owner_id: str
    e2e_test_mode: bool


@dataclass(frozen=True, slots=True)
class ApprovalBinding:
    """The exact external-effect action authorized by a record."""

    action_hash: str
    target_id: str


@dataclass(frozen=True, slots=True)
class SignedApprovalEvent:
    """The authenticated E2E event supplied to the isolated approval path."""

    event: InboundEvent
    signature: str
    secret: bytes


@dataclass(frozen=True, slots=True)
class ExternalEffectRule:
    """A denylist entry that identifies an external-effect operation."""

    rule_id: str
    tool_name_pattern: re.Pattern[str]
    arguments_pattern: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class ExternalEffectDecision:
    """The gate decision returned to the Hermes plugin boundary."""

    external_effect: bool
    allowed: bool
    reason: str | None
    action_hash: str
    target_id: str


def load_denylist(path: str | Path) -> tuple[ExternalEffectRule, ...]:
    """Load the constrained YAML denylist format without an optional dependency."""
    rows = _yaml_rows(Path(path))
    rules: list[ExternalEffectRule] = []
    for row in rows:
        try:
            rule_id = row["id"]
            tool_name_regex = row["tool_name_regex"]
            arguments_regex = row["arguments_regex"]
        except KeyError as error:
            raise DenylistConfigurationError("external-effect rule missing required field") from error
        try:
            rules.append(
                ExternalEffectRule(
                    rule_id=rule_id,
                    tool_name_pattern=re.compile(tool_name_regex),
                    arguments_pattern=re.compile(arguments_regex),
                )
            )
        except re.error as error:
            raise DenylistConfigurationError(f"external-effect rule has invalid regex: {rule_id}") from error
    if not rules:
        raise DenylistConfigurationError("external-effect denylist must contain at least one rule")
    return tuple(rules)


def evaluate_tool_call(
    call: ToolCall,
    rules: tuple[ExternalEffectRule, ...],
    context: ApprovalContext,
) -> ExternalEffectDecision:
    """Allow read-only calls and require a matching owner record for mutations."""
    rule = _matching_rule(call, rules)
    if rule is None:
        return ExternalEffectDecision(False, True, None, "", "")
    target_id = f"tool:{rule.rule_id}:{call.tool_name}"
    action_hash = _action_hash(call, target_id)
    approved = context.approval_log is not None and _has_valid_approval(
        context.approval_log, action_hash, target_id, context.owner_id, context.e2e_test_mode
    )
    return ExternalEffectDecision(True, approved, "approved" if approved else "approval_required", action_hash, target_id)


def approval_challenge(action_hash: str, target_id: str) -> str:
    """Return the exact signed E2E text bound to one external-effect action."""
    return f"APPROVE external-effect {action_hash} target:{target_id}"


def record_signed_e2e_approval(
    context: ApprovalContext,
    binding: ApprovalBinding,
    signed_event: SignedApprovalEvent,
) -> bool:
    """Append an E2E-only approval record after validating its owner-bound HMAC."""
    if context.approval_log is None:
        return False
    event = signed_event.event
    if not accept_test_event(event, signed_event.signature, signed_event.secret, e2e_test_mode=context.e2e_test_mode):
        return False
    if event.user_id != context.owner_id or event.channel_id != "approvals":
        return False
    if event.text != approval_challenge(binding.action_hash, binding.target_id):
        return False
    _append_record(
        context.approval_log,
        {
            "action": "external_effect.approval",
            "approval": {
                "channel": "approvals",
                "message_id": event.event_id,
                "method": "signed_injection_e2e",
                "owner_id": context.owner_id,
            },
            "hash": binding.action_hash,
            "result": {"status": "approved"},
            "target_id": binding.target_id,
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    return True


def _yaml_rows(path: Path) -> tuple[dict[str, str], ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise DenylistConfigurationError("external-effect denylist is unreadable") from error
    rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for source_line in lines:
        line = source_line.strip()
        if not line or line.startswith("#") or line == "rules:":
            continue
        if line.startswith("- "):
            current = {}
            rows.append(current)
            line = line[2:]
        if current is None or ":" not in line:
            raise DenylistConfigurationError("external-effect denylist has unsupported YAML")
        key, value = line.split(":", 1)
        if key not in {"id", "tool_name_regex", "arguments_regex"} or not value.strip():
            raise DenylistConfigurationError("external-effect denylist has invalid field")
        current[key] = value.strip()
    return tuple(rows)


def _matching_rule(call: ToolCall, rules: tuple[ExternalEffectRule, ...]) -> ExternalEffectRule | None:
    arguments = json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    for rule in rules:
        if rule.tool_name_pattern.search(call.tool_name) and rule.arguments_pattern.search(arguments):
            return rule
    return None


def _action_hash(call: ToolCall, target_id: str) -> str:
    payload = {"action": "external_effect.tool_call", "arguments": call.arguments, "target_id": target_id, "tool_name": call.tool_name}
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _has_valid_approval(
    approval_log: Path, action_hash: str, target_id: str, owner_id: str, e2e_test_mode: bool
) -> bool:
    try:
        lines = approval_log.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return False
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        approval = record.get("approval") if isinstance(record, dict) else None
        if not isinstance(approval, dict):
            continue
        method = approval.get("method")
        e2e_allowed = method == "signed_injection_e2e" and e2e_test_mode
        manual_allowed = method == "manual_reaction"
        if not (e2e_allowed or manual_allowed):
            continue
        if (
            record.get("action") == "external_effect.approval"
            and record.get("hash") == action_hash
            and record.get("target_id") == target_id
            and record.get("result") == {"status": "approved"}
            and approval.get("channel") == "approvals"
            and approval.get("owner_id") == owner_id
        ):
            return True
    return False


def _append_record(path: Path, record: dict[str, JsonValue]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    path.chmod(0o600)
