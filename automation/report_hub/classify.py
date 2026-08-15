"""Classify raw #agents-log messages: accept (registered/unregistered) or quarantine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from automation.interop.report import TaskReport, parse_report
from automation.report_hub.registry import PeerRegistry

REGISTERED: Final = "registered"
UNKNOWN_BOT: Final = "unknown_bot"
AGENT_ID_MISMATCH: Final = "agent_id_mismatch"


@dataclass(frozen=True, slots=True)
class AcceptedReport:
    """A conformant protocol report destined for the main reports table."""

    report: TaskReport
    registered: bool
    registration_note: str


@dataclass(frozen=True, slots=True)
class QuarantinedMessage:
    """A non-conformant message that must never reach the main reports table."""

    reason: str


def classify_message(
    content: str,
    author_bot_user_id: str,
    registry: PeerRegistry,
) -> AcceptedReport | QuarantinedMessage:
    """Apply the W1-6 strict parser, then the peer registry identity check.

    - Non-conformant or forged-field payloads (parse_report -> None) are quarantined.
    - Conformant reports from an author absent from the registry are accepted
      but marked unregistered (unknown_bot).
    - Conformant reports whose claimed agent_id does not match the author's
      registered agent_id are accepted but marked unregistered (agent_id_mismatch).
    """
    report = parse_report(content)
    if report is None:
        return QuarantinedMessage(reason="non_conformant_protocol_message")
    registered_agent_id = registry.agent_id_for(author_bot_user_id)
    if registered_agent_id is None:
        return AcceptedReport(report=report, registered=False, registration_note=UNKNOWN_BOT)
    if registered_agent_id != report.agent_id:
        return AcceptedReport(report=report, registered=False, registration_note=AGENT_ID_MISMATCH)
    return AcceptedReport(report=report, registered=True, registration_note=REGISTERED)
