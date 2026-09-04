"""Turn only an admin-approved submission into a temporary publish input."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from automation.typing_compat import override

from automation.group_roster.schema import MemberStatus, Roster
from automation.interop.approval_surface import ApprovalKind
from automation.managed_skills.manifest import ManagedManifest
from automation.managed_skills.principal import PUBLISHER_PRINCIPAL
from automation.managed_skills.submission_artifact import (
    SubmissionArtifact,
    extract_submission,
    validate_submission_artifact,
)
from automation.managed_skills.submission_errors import SubmissionArtifactError
from automation.managed_skills.submission_message import (
    SubmissionIdentity,
    new_submission_envelope,
    parse_submission_message,
)
from automation.managed_skills.submission_transport import (
    SubmissionTransport,
    SubmissionTransportError,
)
from automation.skill_gate_specs import APPROVE_EMOJI, CANCEL_EMOJI
from automation.skill_gate_surface import SupplyChainSurface

_MESSAGE_ID: Final = re.compile(r"[^:\s]+\Z")
_NONCE: Final = re.compile(r"[0-9a-f]{32}\Z")


@dataclass(frozen=True, slots=True)
class SubmissionReviewError(Exception):
    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class SubmissionEvidence:
    message_id: str
    nonce: str

    def __post_init__(self) -> None:
        if _MESSAGE_ID.fullmatch(self.message_id) is None or _NONCE.fullmatch(self.nonce) is None:
            raise SubmissionReviewError("submission evidence must be MESSAGE_ID:SUBMISSION_NONCE")

    @classmethod
    def parse(cls, raw: str) -> SubmissionEvidence:
        message_id, separator, nonce = raw.partition(":")
        if not separator:
            raise SubmissionReviewError("submission evidence must be MESSAGE_ID:SUBMISSION_NONCE")
        return cls(message_id, nonce)


@dataclass(frozen=True, slots=True)
class ApprovedSubmissionConfig:
    artifact: SubmissionArtifact
    evidence: SubmissionEvidence
    roster: Roster
    surface: SupplyChainSurface
    transport: SubmissionTransport


@dataclass(frozen=True, slots=True)
class ApprovedSubmission:
    source_dir: Path
    manifest: ManagedManifest


def _publisher_name(roster: Roster) -> str:
    principal = roster.admin.publisher_principal
    if PUBLISHER_PRINCIPAL.fullmatch(principal) is None:
        raise SubmissionReviewError("roster admin publisher principal is invalid")
    return principal.removeprefix("publisher-").removesuffix("@autophagy")


def _active_submitter(roster: Roster, node_label: str) -> bool:
    return any(
        member.node_label == node_label and member.status is MemberStatus.ACTIVE
        for member in roster.members
    )


def _admin_reacted(
    config: ApprovedSubmissionConfig,
    channel_id: str,
    emoji: str,
) -> bool:
    users = config.transport.reaction_users(
        channel_id,
        config.evidence.message_id,
        emoji,
    )
    return any(
        user.user_id == config.roster.admin.discord_user_id and not user.bot
        for user in users
    )


@contextmanager
def open_approved_submission(config: ApprovedSubmissionConfig) -> Iterator[ApprovedSubmission]:
    """Recheck immutable bytes, current roster authority, message binding, and admin reaction."""
    if config.surface.kind is not ApprovalKind.SKILL_SUBMIT:
        raise SubmissionReviewError("submission review used the wrong approval kind")
    artifact = validate_submission_artifact(
        config.artifact.tarball_path,
        config.artifact.manifest_path,
        expected_tarball_sha256=config.artifact.tarball_sha256,
    )
    if artifact.manifest_sha256 != config.artifact.manifest_sha256:
        raise SubmissionReviewError("submission manifest sha256 differs from its pinned value")
    binding = config.surface.new()
    try:
        message = config.transport.fetch_message(binding.channel_id, config.evidence.message_id)
        if message is None:
            raise SubmissionReviewError("submission approval message is missing")
        envelope = parse_submission_message(message.content)
        expected = new_submission_envelope(
            SubmissionIdentity(config.roster.group_id, envelope.submitter),
            artifact,
            config.evidence.nonce,
        )
        if envelope != expected or message.attachment_names != envelope.attachment_names:
            raise SubmissionReviewError("submission approval binding does not match the artifacts")
        if not _active_submitter(config.roster, envelope.submitter):
            raise SubmissionReviewError("submission author is not an active roster member")
        if artifact.manifest.publisher != _publisher_name(config.roster):
            raise SubmissionReviewError("submission publisher does not match the roster admin")
        cancelled = _admin_reacted(config, binding.channel_id, CANCEL_EMOJI)
        approved = _admin_reacted(config, binding.channel_id, APPROVE_EMOJI)
    except (SubmissionArtifactError, SubmissionTransportError) as error:
        raise SubmissionReviewError(str(error)) from error
    if cancelled:
        raise SubmissionReviewError("submission review was cancelled by the group admin")
    if not approved:
        raise SubmissionReviewError("submission has no group-admin approval")
    with extract_submission(artifact) as source:
        yield ApprovedSubmission(source, artifact.manifest)
