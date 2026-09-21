"""Hash-bound wire message for one personal-to-group review request."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Final, Protocol, TypeAlias, TypeGuard

from automation.managed_skills.submission_artifact import SubmissionArtifact
from automation.managed_skills.submission_errors import SubmissionArtifactError
from automation.stored_content import content_matches, hash_parts

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class _JsonLoader(Protocol):
    def __call__(self, raw: str, /) -> JsonValue: ...


_JSON_LOADS: _JsonLoader = json.loads

MESSAGE_PREFIX: Final = "[personal-skill-submission-v1] "
_MESSAGE_PREFIX_V2: Final = "[personal-skill-submission-v2] "
_MESSAGE_PREFIX_V3: Final = "[personal-skill-submission-v3] "
# v1 본문은 parser의 exact-match 입력이다. 공유 문구 변경을 따라가지 않는다.
_V1_INSTRUCTION: Final = "이 메시지에 ✅ 실행 / ⛔ 취소"
# Frozen read grammar: these persisted delimiters do not follow renderer changes.
_BODY_V2: Final = re.compile(
    r"대상: (?:(?P<skill>.+) )?\((?P<action_hash>sha256:[0-9a-f]{64})\)\n"
    r"사실: (?P<fact>.+) \(승인 요청; 만료: 기한 없음\)\n"
    r"위치: 이 메시지\n"
    r"인계: 소유자: 위 위치 · 반응 ✅ 실행 / ⛔ 취소; 다음: 관리자 명시 발행 시 재검증; 자동 발행 없음\n"
    r"되돌리기: 해당 없음; 취소 시: 제출 검토 취소; 발행하지 않음"
)
_BODY_V3: Final = re.compile(
    r"\*\*🔔 (?P<skill>[^\n]*)\*\*\n"
    + r"> (?P<fact>[^\n]*)\n\n"
    + r"\*\*결정:\*\* 이 메시지에 ✅ 실행 / ⛔ 취소 · 만료 없음\n"
    + r"취소 시: 제출 검토 취소; 발행하지 않음\n"
    + r"다음: 관리자 명시 발행 시 재검증; 자동 발행 없음\n"
    + r"-# 참조: `(?P<reference>[^`\n]+)`"
)
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


def _render_v1(envelope: SubmissionEnvelope) -> str:
    """Frozen v1 wire bytes, including the formerly shared reaction instruction."""
    payload = json.dumps(asdict(envelope), separators=(",", ":"), sort_keys=True)
    content = f"{MESSAGE_PREFIX}{payload}\n{_V1_INSTRUCTION}"
    if len(content) > 1900:
        raise SubmissionArtifactError("submission approval message exceeds 1900 characters")
    return content


def _parse_payload(payload: str) -> SubmissionEnvelope:
    """Validate the stored JSON and action digest shared by both wire versions."""
    try:
        raw = _JSON_LOADS(payload)
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
    canonical = json.dumps(raw, separators=(",", ":"), sort_keys=True)
    if not content_matches(payload, hash_parts(canonical)):
        raise SubmissionArtifactError("submission approval message is not canonical")
    return envelope


def _render_v2(envelope: SubmissionEnvelope) -> str | None:
    """Frozen v2: exact JSON wire line plus five owner fields; None means no capability."""
    try:
        from automation.interop.owner_message import (
            Action, Approval, OwnerMessage, OwnerMessageError, Ref, render,
        )
    except ImportError:
        return None
    location = Ref(scope="self")
    try:
        body = render(
            OwnerMessage(
                subject_key=envelope.action_hash,
                subject=envelope.skill,
                fact=f"그룹 {envelope.group_id} · 제출자 {envelope.submitter}",
                location=location,
                owner=Action("react", location, "✅ 실행 / ⛔ 취소"),
                agent_next="관리자 명시 발행 시 재검증; 자동 발행 없음",
                recovery="not_applicable",
                detail=Approval(expires_at=None, cancel_effect="제출 검토 취소; 발행하지 않음"),
            ),
            destination=location,
        )
    except OwnerMessageError:
        return None
    payload = json.dumps(asdict(envelope), separators=(",", ":"), sort_keys=True)
    return f"{_MESSAGE_PREFIX_V2}{payload}\n{body}"


def render_submission_message(envelope: SubmissionEnvelope) -> str:
    """Select v3 for new cards; fall back only when the optional capability is absent."""
    content = _render_v3(envelope)
    if content is None:
        content = _render_v2(envelope)
    if content is None:
        return _render_v1(envelope)
    if len(content) > 1900:
        raise SubmissionArtifactError("submission approval message exceeds 1900 characters")
    return content


def _render_v3(envelope: SubmissionEnvelope) -> str | None:
    """New presentation with the same immutable JSON and semantic action hash."""
    try:
        from automation.interop.owner_message import Action, Approval, OwnerMessage, OwnerMessageError, Ref, render
    except ImportError:
        return None
    here = Ref(scope="self")
    try:
        body = render(OwnerMessage(
            subject_key=envelope.action_hash, subject=envelope.skill,
            fact=" ".join(f"그룹 {envelope.group_id} · 제출자 {envelope.submitter}".split()),
            location=here, owner=Action("react", here, "✅ 실행 / ⛔ 취소"),
            agent_next="관리자 명시 발행 시 재검증; 자동 발행 없음",
            recovery="not_applicable",
            detail=Approval(None, "제출 검토 취소; 발행하지 않음"),
            render_version="owner-ko-v2",
        ), destination=here)
    except OwnerMessageError:
        return None
    payload = json.dumps(asdict(envelope), separators=(",", ":"), sort_keys=True)
    return f"{body}\n-# {_MESSAGE_PREFIX_V3}{payload}"


def _body_matches(body: str, envelope: SubmissionEnvelope) -> bool:
    """Read captured display fields against the wire; never generate approval text."""
    matched = _BODY_V2.fullmatch(body)
    if matched is None:
        return False
    # v2 display whitespace is folded; the JSON/action hash remains exact.
    return (
        (matched.group("skill") or "") == " ".join(envelope.skill.split())
        and matched.group("action_hash") == envelope.action_hash
        and matched.group("fact") == " ".join(
            f"그룹 {envelope.group_id} · 제출자 {envelope.submitter}".split()
        )
    )


def parse_submission_message(content: str) -> SubmissionEnvelope:
    """Read frozen v1/v2/v3 grammars and digests; never invoke a renderer."""
    if len(content) > 1900:
        raise SubmissionArtifactError("submission approval message exceeds 1900 characters")
    body, separator, footer = content.rpartition("\n")
    prefix = _MESSAGE_PREFIX_V3 if footer.startswith(f"-# {_MESSAGE_PREFIX_V3}") else None
    payload = footer.removeprefix(f"-# {_MESSAGE_PREFIX_V3}")
    if prefix is None:
        first, separator, body = content.partition("\n")
        prefix = next((prefix for prefix in (MESSAGE_PREFIX, _MESSAGE_PREFIX_V2)
                       if first.startswith(prefix)), None)
        payload = first.removeprefix(prefix) if prefix is not None else ""
    if not separator or prefix is None:
        raise SubmissionArtifactError("submission approval message has invalid framing")
    envelope = _parse_payload(payload)
    if prefix == _MESSAGE_PREFIX_V3:
        matched = _BODY_V3.fullmatch(body)
        valid_body = matched is not None and (
            matched.group("skill") == " ".join(envelope.skill.split())
            and matched.group("fact") == " ".join(f"그룹 {envelope.group_id} · 제출자 {envelope.submitter}".split())
            and matched.group("reference") == envelope.action_hash[:8]
        )
    else:
        valid_body = body == _V1_INSTRUCTION if prefix == MESSAGE_PREFIX else _body_matches(body, envelope)
    if not valid_body:
        raise SubmissionArtifactError("submission approval message is not canonical")
    return envelope
