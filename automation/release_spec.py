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

_RELEASE_VERSION: Final = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+\Z")
_COMMIT_SHA: Final = re.compile(r"[0-9a-f]{40}\Z")
_NONCE: Final = re.compile(r"[0-9a-f]{32}\Z")
_SURFACE_NAME: Final = re.compile(r"[a-z0-9][a-z0-9:._/-]{0,99}\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")


class ReleaseSpecError(ValueError):
    """A release approval cannot be rendered or bound safely."""


def fit_patch_notes(
    *,
    version: str,
    head_sha: str,
    surface_digests: tuple[tuple[str, str], ...],
    patch_notes: str,
) -> str:
    """Fit generated note lines while raw specs remain fail-closed."""
    lines = patch_notes.rstrip().splitlines()
    for kept in range(len(lines), -1, -1):
        omitted = len(lines) - kept
        candidate_lines = lines[:kept]
        if omitted:
            candidate_lines.append(f"- ... +{omitted} patch-note line(s) omitted")
        candidate = "\n".join(candidate_lines)
        try:
            ReleaseSpec(
                version=version,
                head_sha=head_sha,
                release_nonce="0" * 32,
                surface_digests=surface_digests,
                patch_notes=candidate,
            ).render()
        except ReleaseSpecError as error:
            if "exceeds 1900 characters" in str(error):
                continue
            raise
        return candidate
    raise ReleaseSpecError("fixed release fields leave no room for bounded patch notes")


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
    )


@dataclass(frozen=True, slots=True)
class ReleaseSpec:
    """release: one owner decision over version + HEAD + the complete surface digest set."""

    version: str
    head_sha: str
    release_nonce: str
    surface_digests: tuple[tuple[str, str], ...]
    patch_notes: str
    render_version: int = 2

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
        if self.render_version not in (1, 2):
            raise ReleaseSpecError("release render version must be 1 or 2")
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

    def render(self) -> str:
        if self.render_version == 1:
            surfaces = "\n".join(
                f"- surface `{name}`: `{digest}`"
                for name, digest in self.surface_digests
            ) or "- surface: 변경 없음"
            content = (
                f"[release] {self.version} 배포 승인 요청\n"
                f"- version: `{self.version}`\n"
                f"- HEAD: `{self.head_sha}`\n"
                f"- release_nonce: `{self.release_nonce}`\n"
                f"{surfaces}\n"
                "- 패치노트:\n"
                f"{self.patch_notes.rstrip()}\n"
                f"{_APPROVAL_LINE}"
            )
        else:
            content = self._render_v2()
        if len(content) > 1900:
            raise ReleaseSpecError(
                f"release approval message exceeds 1900 characters: {len(content)}"
            )
        return content

    def _render_v2(self) -> str:
        surfaces = ", ".join(f"`{name}`" for name, _digest in self.surface_digests)
        surfaces = surfaces or "변경 없음"
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
