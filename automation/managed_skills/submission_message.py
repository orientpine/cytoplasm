"""Hash-bound wire message for one personal-to-group review request."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Final, Protocol, TypeAlias, TypeGuard

from automation.interop.approval_surface import (
    ApprovalKind,
    reaction_instruction,
    required_surface,
)
from automation.managed_skills.submission_artifact import SubmissionArtifact
from automation.managed_skills.submission_errors import SubmissionArtifactError

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class _JsonLoader(Protocol):
    def __call__(self, raw: str, /) -> JsonValue: ...


_JSON_LOADS: _JsonLoader = json.loads

MESSAGE_PREFIX: Final = "[personal-skill-submission-v1] "
_FIELDS: Final = frozenset(
    {
        "action_hash",
        "group_id",
        "manifest_filename",
        "manifest_sha256",
        "nonce",
        "skill",
        "skill_sha256",
        "source_commit",
        "submitter",
        "tarball_filename",
        "tarball_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class SubmissionIdentity:
    group_id: str
    submitter: str


@dataclass(frozen=True, slots=True)
class SubmissionEnvelope:
    action_hash: str
    group_id: str
    manifest_filename: str
    manifest_sha256: str
    nonce: str
    skill: str
    skill_sha256: str
    source_commit: str
    submitter: str
    tarball_filename: str
    tarball_sha256: str

    @property
    def attachment_names(self) -> tuple[str, str]:
        return self.tarball_filename, self.manifest_filename


def _is_json_object(value: JsonValue) -> TypeGuard[dict[str, JsonValue]]:
    return isinstance(value, dict)


def _required(payload: dict[str, JsonValue], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise SubmissionArtifactError(f"submission message has invalid {field}")
    return value


def _semantic_hash(envelope: SubmissionEnvelope) -> str:
    payload = {
        "group_id": envelope.group_id,
        "manifest_filename": envelope.manifest_filename,
        "manifest_sha256": envelope.manifest_sha256,
        "skill": envelope.skill,
        "skill_sha256": envelope.skill_sha256,
        "source_commit": envelope.source_commit,
        "submitter": envelope.submitter,
        "tarball_filename": envelope.tarball_filename,
        "tarball_sha256": envelope.tarball_sha256,
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def new_submission_envelope(
    identity: SubmissionIdentity,
    artifact: SubmissionArtifact,
    nonce: str,
) -> SubmissionEnvelope:
    """Bind one review request to both immutable attachment byte streams."""
    manifest = artifact.manifest
    if manifest.source_commit is None:
        raise SubmissionArtifactError("submitted manifest must name its personal source commit")
    provisional = SubmissionEnvelope(
        action_hash="pending",
        group_id=identity.group_id,
        manifest_filename=artifact.manifest_path.name,
        manifest_sha256=artifact.manifest_sha256,
        nonce=nonce,
        skill=manifest.skill,
        skill_sha256=manifest.skill_sha256,
        source_commit=manifest.source_commit,
        submitter=identity.submitter,
        tarball_filename=artifact.tarball_path.name,
        tarball_sha256=artifact.tarball_sha256,
    )
    return SubmissionEnvelope(**{**asdict(provisional), "action_hash": _semantic_hash(provisional)})


def render_submission_message(envelope: SubmissionEnvelope) -> str:
    """Render canonical machine data plus the shared surface-neutral reaction instruction."""
    payload = json.dumps(asdict(envelope), separators=(",", ":"), sort_keys=True)
    instruction = reaction_instruction(
        ApprovalKind.SKILL_SUBMIT,
        required_surface(ApprovalKind.SKILL_SUBMIT),
    )
    content = f"{MESSAGE_PREFIX}{payload}\n{instruction}"
    if len(content) > 1900:
        raise SubmissionArtifactError("submission approval message exceeds 1900 characters")
    return content


def parse_submission_message(content: str) -> SubmissionEnvelope:
    """Parse the exact v1 review binding and reject non-canonical or altered content."""
    lines = content.splitlines()
    if len(lines) != 2 or not lines[0].startswith(MESSAGE_PREFIX):
        raise SubmissionArtifactError("submission approval message has invalid framing")
    try:
        raw = _JSON_LOADS(lines[0].removeprefix(MESSAGE_PREFIX))
    except json.JSONDecodeError as error:
        raise SubmissionArtifactError("submission approval message has invalid JSON") from error
    if not _is_json_object(raw) or frozenset(raw) != _FIELDS:
        raise SubmissionArtifactError("submission approval message has invalid fields")
    envelope = SubmissionEnvelope(
        action_hash=_required(raw, "action_hash"),
        group_id=_required(raw, "group_id"),
        manifest_filename=_required(raw, "manifest_filename"),
        manifest_sha256=_required(raw, "manifest_sha256"),
        nonce=_required(raw, "nonce"),
        skill=_required(raw, "skill"),
        skill_sha256=_required(raw, "skill_sha256"),
        source_commit=_required(raw, "source_commit"),
        submitter=_required(raw, "submitter"),
        tarball_filename=_required(raw, "tarball_filename"),
        tarball_sha256=_required(raw, "tarball_sha256"),
    )
    if envelope.action_hash != _semantic_hash(envelope):
        raise SubmissionArtifactError("submission approval action hash is invalid")
    if render_submission_message(envelope) != content:
        raise SubmissionArtifactError("submission approval message is not canonical")
    return envelope
