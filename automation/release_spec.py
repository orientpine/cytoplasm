"""What a release approval binds the owner's ✅ to: version, HEAD, surface digest set.

Split out of ``skill_gate_specs`` under the repo's 250 pure-LOC ceiling (AS-1.11
precedent) — adding ``ReleaseSpec`` there pushed that module to 365 pure LOC. The
shared primitives (``_hash``, ``StoredBinding``, ``binding_fields``, the approval
line) stay in ``skill_gate_specs`` and are imported here, so there is exactly one
copy of each; only the release-shaped spec lives in this module.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from automation.interop.approval_surface import ApprovalBinding
from automation.skill_gate_specs import (
    _APPROVAL_LINE,
    StoredBinding,
    _hash,
    binding_fields,
)

RELEASE_ACTION: Final = "release.deploy"
#: 카드 한 통과 상세 메시지 한 통의 상한 — Discord 한도 2000 아래의 같은 여유폭.
MESSAGE_LIMIT: Final = 1900

_RELEASE_VERSION: Final = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+\Z")
_COMMIT_SHA: Final = re.compile(r"[0-9a-f]{40}\Z")
_NONCE: Final = re.compile(r"[0-9a-f]{32}\Z")
_SURFACE_NAME: Final = re.compile(r"[a-z0-9][a-z0-9:._/-]{0,99}\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")


class ReleaseSpecError(ValueError):
    """A release approval cannot be rendered or bound safely."""


def detail_header(version: str, head_sha: str, index: int, total: int) -> str:
    """상세 메시지 머리글 — 매 통이 어느 릴리스·어느 배포 기준의 것인지 스스로 말한다."""
    return f"[release] {version} 변경 상세 ({index}/{total}) — 기준 {head_sha[:12]}"


def _packed(body: str, budget: int) -> list[str]:
    """줄 단위로 채운다 — 한도보다 긴 줄만 이어붙일 수 있게 쪼개고, 버리지는 않는다."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in body.splitlines():
        for piece in [line[at : at + budget] for at in range(0, len(line), budget)] or [""]:
            if current and size + len(piece) + 1 > budget:
                chunks.append("\n".join(current))
                current, size = [piece], len(piece)
                continue
            size += len(piece) + (1 if current else 0)
            current.append(piece)
    if current:
        chunks.append("\n".join(current))
    return chunks or [""]


def split_messages(*, version: str, head_sha: str, body: str) -> tuple[str, ...]:
    """본문을 한도 안의 메시지들로 나눈다.

    스펙 쪽에 두는 이유: 카드가 '아래 k개 메시지' 라고 약속하고, 노드의 게이트는 저장된
    레코드만으로 그 k 와 본문을 재생해야 한다 — 계획(git·매니페스트) 없이도 되어야 한다.
    """
    total = 1
    for _ in range(8):
        budget = MESSAGE_LIMIT - len(detail_header(version, head_sha, total, total)) - 1
        chunks = _packed(body, budget)
        if len(chunks) == total:
            return tuple(
                f"{detail_header(version, head_sha, index, total)}\n{chunk}"
                for index, chunk in enumerate(chunks, start=1)
            )
        total = len(chunks)
    raise ReleaseSpecError("release detail messages do not converge on a stable count")


def spec_from_record(record: Mapping[str, str]) -> ReleaseSpec:
    """Replay a stored pending record into the exact spec the owner decided on.

    Single copy on purpose: the producer's decision polling AND the staged gate's
    ``release-authorize`` both replay records, and two replays drift apart exactly
    where a drifted replay authorizes the wrong bytes.
    """
    raw = json.loads(record.get("surface_digests", "[]"))
    if not isinstance(raw, list):
        raise ReleaseSpecError("stored surface digests are malformed")
    try:
        render_version = int(record.get("render_version", "1"))
    except ValueError as error:
        raise ReleaseSpecError("stored render version is malformed") from error
    return ReleaseSpec(
        version=record.get("version", ""),
        head_sha=record.get("head_sha", ""),
        release_nonce=record.get("release_nonce", ""),
        surface_digests=tuple((str(row[0]), str(row[1])) for row in raw),
        patch_notes=record.get("patch_notes", ""),
        render_version=render_version,
        major_note=record.get("major_note", ""),
    )


def spec_from_plan(payload: Mapping[str, object], release_nonce: str) -> ReleaseSpec:
    """One immutable spec from the plan JSON `release.sh` carries between steps.

    `spec_from_record` 의 형제다 — 둘 다 바깥 표현(계획 JSON · 저장 레코드)을 같은 스펙으로
    되살리므로 한 자리에 둔다. 2026-09-10 에 `release_approval` 에서 옮겼다: 그 모듈이 250
    pure-LOC 천장을 넘겼고, 이 함수의 집은 원래 여기다(이 모듈 자신이 같은 이유로 갈라졌다).
    """
    surfaces = payload.get("surface_digests")
    if not isinstance(surfaces, list):
        raise ReleaseSpecError("plan payload carries no surface digest list")
    return ReleaseSpec(
        version=str(payload.get("version", "")),
        head_sha=str(payload.get("head", "")),
        release_nonce=release_nonce,
        surface_digests=tuple((str(row[0]), str(row[1])) for row in surfaces),
        patch_notes=str(payload.get("patch_notes", "")),
        major_note=str(payload.get("major_note", "")),
    )


@dataclass(frozen=True, slots=True)
class ReleaseSpec:
    """release: one owner decision over version + HEAD + the complete surface digest set."""

    version: str
    head_sha: str
    release_nonce: str
    surface_digests: tuple[tuple[str, str], ...]
    patch_notes: str
    render_version: int = 3
    major_note: str = ""

    def __post_init__(self) -> None:
        if _RELEASE_VERSION.fullmatch(self.version) is None:
            raise ReleaseSpecError(f"invalid release version: {self.version!r}")
        if _COMMIT_SHA.fullmatch(self.head_sha) is None:
            raise ReleaseSpecError("release HEAD must be a 40-character lowercase sha")
        if _NONCE.fullmatch(self.release_nonce) is None:
            raise ReleaseSpecError("release nonce must be 32 lowercase hex characters")
        try:
            rows = tuple(sorted(self.surface_digests))
        except (TypeError, ValueError) as error:
            raise ReleaseSpecError("surface digests are malformed") from error
        if len({name for name, _digest in rows}) != len(rows):
            raise ReleaseSpecError("surface names must be unique")
        if any(
            _SURFACE_NAME.fullmatch(name) is None or _SHA256.fullmatch(digest) is None
            for name, digest in rows
        ):
            raise ReleaseSpecError("surface names and sha256 digests must be canonical")
        if not isinstance(self.patch_notes, str) or not self.patch_notes.strip():
            raise ReleaseSpecError("release patch notes must not be empty")
        if self.render_version not in (1, 2, 3):
            raise ReleaseSpecError("release render version must be 1, 2 or 3")
        if "\n" in self.major_note:
            raise ReleaseSpecError("the operator note must stay on one card line")
        object.__setattr__(self, "surface_digests", rows)

    def key(self) -> str:
        return "release"

    def record_name(self) -> str:
        return "release"

    def action_hash(self) -> str:
        return _hash(
            "release",
            self.version,
            self.head_sha,
            *(f"{name}={digest}" for name, digest in self.surface_digests),
        )

    def stored(self, record: Mapping[str, str]) -> StoredBinding | None:
        action_hash = record.get("action_hash", "")
        message_id = record.get("message_id", "")
        nonce = record.get("release_nonce", "")
        if not action_hash or not message_id or not nonce:
            return None
        return StoredBinding(action_hash, message_id, nonce)

    def detail_messages(self) -> tuple[str, ...]:
        """카드가 가리키는 변경 상세 메시지 전부 — 레코드의 원문에서 그대로 재생된다."""
        return split_messages(
            version=self.version, head_sha=self.head_sha, body=self.patch_notes
        )

    def render(self) -> str:
        """게시할 카드 본문. 버전형이며 과거 판본은 저장된 레코드를 위해 동결이다."""
        renderers = {1: self._render_v1, 2: self._render_v2, 3: self._render_v3}
        content = renderers[self.render_version]()
        if len(content) > 1900:
            raise ReleaseSpecError(
                f"release approval message exceeds 1900 characters: {len(content)}"
            )
        return content

    def _bundle_names(self) -> str:
        return ", ".join(f"`{name}`" for name, _digest in self.surface_digests) or "변경 없음"

    def _render_v1(self) -> str:
        surfaces = "\n".join(
            f"- surface `{name}`: `{digest}`"
            for name, digest in self.surface_digests
        ) or "- surface: 변경 없음"
        return (
            f"[release] {self.version} 배포 승인 요청\n"
            f"- version: `{self.version}`\n"
            f"- HEAD: `{self.head_sha}`\n"
            f"- release_nonce: `{self.release_nonce}`\n"
            f"{surfaces}\n"
            "- 패치노트:\n"
            f"{self.patch_notes.rstrip()}\n"
            f"{_APPROVAL_LINE}"
        )

    def _render_v3(self) -> str:
        """v3: 카드는 무엇을 승인하는지만 싣고, 변경 원문은 뒤따르는 상세 메시지가 싣는다."""
        operator = f"{self.major_note}\n" if self.major_note else ""
        return (
            f"[release] {self.version} 배포 승인 요청\n"
            f"- 배포 기준: `{self.head_sha}`\n"
            f"- 배포 번들 ({len(self.surface_digests)}): {self._bundle_names()}\n"
            f"- 승인 바인딩: `{self.action_hash()}`\n"
            f"{operator}"
            f"- 변경 상세: 아래 {len(self.detail_messages())}개 메시지"
            f" (같은 릴리스 {self.version} · 기준 {self.head_sha[:12]})\n"
            f"{_APPROVAL_LINE}"
        )

    def _render_v2(self) -> str:
        surfaces = self._bundle_names()
        return (
            f"[release] {self.version} 배포 승인 요청\n"
            f"- 배포 기준: `{self.head_sha}`\n"
            f"- 배포 번들 ({len(self.surface_digests)}): {surfaces}\n"
            f"- 승인 바인딩: `{self.action_hash()}`\n"
            "- 변경 내용:\n"
            f"{self.patch_notes.rstrip()}\n"
            f"{_APPROVAL_LINE}"
        )

    def new_record(self, message_id: str, binding: ApprovalBinding) -> dict[str, str]:
        return {
            "version": self.version,
            "head_sha": self.head_sha,
            "release_nonce": self.release_nonce,
            "surface_digests": json.dumps(
                self.surface_digests, separators=(",", ":"), ensure_ascii=True
            ),
            "patch_notes": self.patch_notes,
            "render_version": str(self.render_version),
            "major_note": self.major_note,
            "message_id": message_id,
            "action_hash": self.action_hash(),
            "approval_action": RELEASE_ACTION,
            "approval_destination": f"release:{self.version}",
            **binding_fields(binding),
        }

    def serialize(self, record: Mapping[str, str]) -> str:
        return json.dumps(dict(record), sort_keys=True)

    def bound(self, content: str, record: Mapping[str, str]) -> bool:
        try:
            replay = spec_from_record(record)
        except (json.JSONDecodeError, ReleaseSpecError, TypeError):
            return False
        return (
            record.get("action_hash", "") == replay.action_hash() == self.action_hash()
            and content == replay.render()
        )
