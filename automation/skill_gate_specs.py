"""What each skill gate binds the owner's ✅ to: message text, record shape, action hash.

One spec per gate — ``skill-deploy``/``skill-publish`` (``ReleaseSpec``: ``release_spec.py``). The ``action_hash`` is a
pure function of every authorizing field the request message DISPLAYS and excludes
the random nonce, so an unchanged request reuses its live message instead of
orphaning it. The nonce is supplied per run and persisted only on a real post.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, TypeAlias

from automation.interop.approval_surface import ApprovalBinding

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class JsonLoader(Protocol):
    def __call__(self, raw: str, /) -> JsonValue: ...


_JSON_LOADS: JsonLoader = json.loads

DEPLOY_ACTION: Final = "skill.deploy"
PUBLISH_ACTION: Final = "skill.publish"

APPROVE_EMOJI: Final = "\u2705"  # ✅ WHITE HEAVY CHECK MARK
CANCEL_EMOJI: Final = "\u26d4"  # ⛔ NO ENTRY
_PERSONAL_HEAD: Final = re.compile(r"[0-9a-f]{40,64}\Z")
#: First line of deploy requests posted before #199 — still live, still must resolve.
_LEGACY_DEPLOY_HEADER: Final = "[skill-deploy] 승인 요청\n"
_APPROVAL_LINE: Final = f"- 승인 방법: 이 메시지에 cha가 {APPROVE_EMOJI} 리액션 (소유자 전용 — 봇/타인 리액션은 거부됨)"


class ProvenanceError(ValueError):
    """A deploy provenance file does not match a supported fail-closed schema."""


def mask(snowflake: str) -> str:
    return f"…{snowflake[-4:]}" if len(snowflake) > 4 else "<short>"


def _hash(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def binding_fields(binding: ApprovalBinding) -> dict[str, str]:
    """The four fields a new record persists so every later read replays them (SI-1)."""
    return {
        "channel_id": binding.channel_id,
        "kind": binding.kind.value,
        "policy_version": str(binding.policy_version),
        "surface": binding.surface.value,
    }


@dataclass(frozen=True, slots=True)
class StoredBinding:
    """A readable pending record; an empty action hash marks migration-only legacy state."""

    action_hash: str
    message_id: str
    nonce: str


@dataclass(frozen=True, slots=True)
class Provenance:
    """Source provenance a deploy request displays and binds to the owner's decision."""

    lines: str
    tag: str
    manifest_sha256: str
    personal_head_sha: str = ""


def _load_provenance(path: Path) -> dict[str, JsonValue]:
    raw = _JSON_LOADS(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ProvenanceError("deploy provenance must be a JSON object")
    return raw


def provenance_lines(path: Path) -> str:
    """Masked provenance suffix appended AFTER a deploy request's binding block."""
    provenance = _load_provenance(path)
    personal_head = provenance.get("personal_head_sha")
    if isinstance(personal_head, str):
        return f"\n- provenance: personal HEAD `{personal_head}`"
    release_sequence = provenance["release_sequence"]
    if not isinstance(release_sequence, str | int) or isinstance(release_sequence, bool):
        raise ProvenanceError("release sequence must be a string or integer")
    return (
        f"\n- provenance: publisher {mask(str(provenance['publisher']))}"
        f" / tag `{provenance['tag']}` / sequence {int(release_sequence)}"
        f" / manifest-sha256 `{provenance['manifest_sha256']}`"
    )


def provenance_of(value: str) -> Provenance:
    """Read ``--provenance-file`` once: the displayed suffix AND the fields it authorizes."""
    if not value:
        return Provenance("", "", "")
    path = Path(value)
    row = _load_provenance(path)
    personal_head = row.get("personal_head_sha")
    if personal_head is not None:
        if (
            not isinstance(personal_head, str)
            or _PERSONAL_HEAD.fullmatch(personal_head) is None
            or set(row) != {"personal_head_sha"}
        ):
            raise ProvenanceError("personal provenance must contain only a committed HEAD sha")
        return Provenance(provenance_lines(path), "", "", personal_head)
    return Provenance(provenance_lines(path), str(row["tag"]), str(row["manifest_sha256"]))


@dataclass(frozen=True, slots=True)
class DeploySpec:
    """skill-deploy: the owner authorizes skill + artifact digest + release provenance."""

    skill: str
    digest: str
    deploy_nonce: str
    review_status: str
    provenance: Provenance
    binding: re.Pattern[str]
    peer_attest_mode: str = "discord"
    peer_status: str = ""

    def key(self) -> str:
        return f"skill-deploy:{self.skill}"

    def record_name(self) -> str:
        return self.skill

    def action_hash(self) -> str:
        return self._hash_of(self.digest)

    def _hash_of(self, digest: str) -> str:
        parts = ("skill-deploy", self.skill, digest, self.provenance.tag, self.provenance.manifest_sha256)
        if self.provenance.personal_head_sha:
            parts = (*parts, "personal-head", self.provenance.personal_head_sha)
        if self.peer_attest_mode == "signed":
            parts = (*parts, "peer-attest", "signed")
        return _hash(*parts)

    def stored(self, record: Mapping[str, str]) -> StoredBinding | None:
        digest, message_id = record.get("hash", ""), record.get("message_id", "")
        nonce = record.get("deploy_nonce", "")
        action_hash = record.get("action_hash", "")
        if not digest or not message_id or (action_hash and not nonce):
            return None
        return StoredBinding(action_hash, message_id, nonce)

    def header(self) -> str:
        # 첫 줄에 스킬명을 넣는다 — Hermes 가 이 메시지의 **앞 80자**를 스레드 제목으로
        # 쓰는데(`_derive_auto_thread_name`), 예전 첫 줄은 `[skill-deploy] 승인 요청` 이라
        # 그 창이 sha256 한복판에서 끝나 16개 요청의 제목이 전부 같아 보였다.
        return f"[skill-deploy] {self.skill} 배포 승인 요청\n"

    def render(self) -> str:
        peer_status = f"{self.peer_status}\n" if self.peer_status else ""
        return self.header() + (
            f"- skill: `{self.skill}`\n"
            f"- sha256: `{self.digest}`\n"
            f"- deploy_nonce: `{self.deploy_nonce}`\n"
            f"{self.review_status}\n"
            "- sandbox: PASS (peer 인스턴스, DUMMY 시크릿)\n"
            f"{peer_status}"
            f"{_APPROVAL_LINE}"
        ) + self.provenance.lines

    def new_record(self, message_id: str, binding: ApprovalBinding) -> dict[str, str]:
        # What a later run must REPLAY to recognise this message as ours: the personal
        # HEAD (already the binding) or, for a release, the rendered provenance suffix —
        # the next release renders a different tag/sequence, and re-rendering with THAT
        # would never equal the message the owner is looking at.
        provenance_fields = (
            {"personal_head_sha": self.provenance.personal_head_sha}
            if self.provenance.personal_head_sha
            else {"provenance_lines": self.provenance.lines} if self.provenance.lines else {}
        )
        return {
            "deploy_nonce": self.deploy_nonce,
            "hash": self.digest,
            "message_id": message_id,
            "action_hash": self.action_hash(),
            "approval_action": DEPLOY_ACTION,
            "approval_destination": f"skill:{self.skill}",
            **provenance_fields,
            **binding_fields(binding),
        }

    def serialize(self, record: Mapping[str, str]) -> str:
        return json.dumps(dict(record))

    def bound(self, content: str, record: Mapping[str, str]) -> bool:
        if record.get("personal_head_sha", "") != self.provenance.personal_head_sha:
            return False
        # Replay what THIS record posted, not what this run would post: the record's
        # digest + nonce, its own provenance suffix (a release that moved on renders a
        # different one), and either header form — requests posted before #199 open with
        # the legacy first line, and the regex already admits both; holding them to the
        # new header made every pre-#199 request impossible to supersede OR consume
        # (2026-08-21: 13 live requests, 0 of them resolvable).
        lines = record.get("provenance_lines", self.provenance.lines)
        expected = replace(
            self,
            digest=record.get("hash", ""),
            deploy_nonce=record.get("deploy_nonce", ""),
            provenance=replace(self.provenance, lines=lines),
        ).render()
        legacy = _LEGACY_DEPLOY_HEADER + expected.removeprefix(self.header())
        if content not in (expected, legacy):
            return False
        matched = self.binding.match(content)
        if matched is None:
            return False
        return (matched.group("skill"), matched.group("digest"), matched.group("nonce")) == (
            self.skill,
            record.get("hash", ""),
            record.get("deploy_nonce", ""),
        )


@dataclass(frozen=True, slots=True)
class PublishSpec:
    """skill-publish: the owner authorizes skill digest + manifest digest + tag."""

    skill: str
    digest: str
    manifest_hash: str
    tag: str
    publish_nonce: str
    binding: re.Pattern[str]

    def key(self) -> str:
        return f"skill-publish:{self.skill}"

    def record_name(self) -> str:
        return f"publish-{self.skill}"

    def action_hash(self) -> str:
        return _hash("skill-publish", self.skill, self.digest, self.manifest_hash, self.tag)

    def stored(self, record: Mapping[str, str]) -> StoredBinding | None:
        digest, manifest = record.get("hash", ""), record.get("manifest_hash", "")
        tag, message_id = record.get("tag", ""), record.get("message_id", "")
        nonce = record.get("publish_nonce", "")
        action_hash = record.get("action_hash", "")
        if not digest or not message_id:
            return None
        if action_hash and (not manifest or not tag or not nonce):
            return None
        return StoredBinding(action_hash, message_id, nonce)

    def render(self) -> str:
        return (
            "[skill-publish] 발행 승인 요청\n"
            f"- skill: `{self.skill}`\n"
            f"- sha256: `{self.digest}`\n"
            f"- manifest_sha256: `{self.manifest_hash}`\n"
            f"- tag: `{self.tag}`\n"
            f"- publish_nonce: `{self.publish_nonce}`\n"
            f"{_APPROVAL_LINE}"
        )

    def new_record(self, message_id: str, binding: ApprovalBinding) -> dict[str, str]:
        return {
            "hash": self.digest,
            "manifest_hash": self.manifest_hash,
            "message_id": message_id,
            "publish_nonce": self.publish_nonce,
            "tag": self.tag,
            "action_hash": self.action_hash(),
            "approval_action": PUBLISH_ACTION,
            "approval_destination": f"skill:{self.skill}",
            **binding_fields(binding),
        }

    def serialize(self, record: Mapping[str, str]) -> str:
        return json.dumps(dict(record), sort_keys=True)

    def bound(self, content: str, record: Mapping[str, str]) -> bool:
        expected = replace(
            self,
            digest=record.get("hash", ""),
            manifest_hash=record.get("manifest_hash", ""),
            tag=record.get("tag", ""),
            publish_nonce=record.get("publish_nonce", ""),
        ).render()
        if content != expected:
            return False
        matched = self.binding.match(content)
        if matched is None:
            return False
        return (
            matched.group("skill"),
            matched.group("digest"),
            matched.group("manifest"),
            matched.group("tag"),
            matched.group("nonce"),
        ) == (
            self.skill,
            record.get("hash", ""),
            record.get("manifest_hash", ""),
            record.get("tag", ""),
            record.get("publish_nonce", ""),
        )


if TYPE_CHECKING:
    from automation.release_spec import ReleaseSpec

# 문자열 전방 참조 — release_spec 이 이 모듈의 공유 프리미티브를 import 하므로(단일 사본),
# 여기서 런타임 import 를 되돌리면 순환이 된다. 이 alias 는 annotation 에서만 쓰인다.
GateSpec: TypeAlias = "DeploySpec | PublishSpec | ReleaseSpec"
