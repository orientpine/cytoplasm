"""Pure owner-approval surface policy.

S1  Per-kind transitions are append-only and version ordered.
S2  A stored binding is interpreted at its stamped policy version, never the
    current version, and a contradictory stored surface fails closed.
S3  Every binding carries a concrete Discord channel id whose facts match its
    explicit surface; directory failures never trigger a fallback.
S4  This module performs no disk, environment, or network I/O. Collaborators
    provide all channel discovery and facts through ``ChannelDirectory``.
S5  v7 (2026-08-24 owner decision) moves owner-only approvals off the owner DM
    into per-kind threads under the personal ``#agent-chat`` channel so the
    owner reads approvals inside threads. Repair stays on the owner DM until
    its Ops bot joins the personal guild.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol, TypeAlias, assert_never


class ApprovalSurfaceError(RuntimeError):
    pass


class ApprovalKind(StrEnum):
    MAIL_REPLY = "mail-reply"
    MAIL_COMPOSE = "mail-compose"
    BUDGET_MAIL = "budget-mail"
    PATENT_EXPORT = "patent-export"
    REPAIR = "repair"
    CALENDAR = "calendar"
    COORDINATION = "coordination"
    WIKI = "wiki"
    SKILL_DEPLOY = "skill-deploy"
    SKILL_ATTEST = "skill-attest"
    SKILL_PUBLISH = "skill-publish"
    SKILL_SUBMIT = "skill-submit"
    MANAGED_ACTIVATE = "managed-activate"
    RELEASE = "release"
    OBSIDIAN_WRITE = "obsidian-write"
    TODO = "todo"


class ApprovalSurface(StrEnum):
    OWNER_DM = "owner-dm"
    SKILL_APPROVALS = "skill-approvals"
    AGENT_CHAT_THREAD = "agent-chat-thread"


# v8 (2026-08-28 §10-7): Ops 봇의 개인 서버 초대를 전제로 repair 승인도 agent-chat
# 스레드로 이관한다 — 마지막 DM 잔존이 0이 된다. 초대 전 배포는 새 repair 승인을
# fail-closed(게시 불가)로 만드므로, 릴리스 컷은 초대 확인 뒤에만 한다(계획 ON-4).
POLICY_VERSION: Final = 8

TransitionLedger: TypeAlias = Mapping[
    ApprovalKind,
    tuple[tuple[int, ApprovalSurface], ...],
]

_TRANSITIONS: Final[TransitionLedger] = MappingProxyType({
    ApprovalKind.MAIL_REPLY: (
        (0, ApprovalSurface.SKILL_APPROVALS),
        (2, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.MAIL_COMPOSE: (
        (0, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.BUDGET_MAIL: (
        (0, ApprovalSurface.SKILL_APPROVALS),
        (3, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.PATENT_EXPORT: (
        (0, ApprovalSurface.SKILL_APPROVALS),
        (4, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.REPAIR: (
        (0, ApprovalSurface.SKILL_APPROVALS),
        (5, ApprovalSurface.OWNER_DM),
        (8, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.CALENDAR: (
        (0, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.COORDINATION: (
        (0, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.WIKI: (
        (0, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.SKILL_DEPLOY: ((0, ApprovalSurface.SKILL_APPROVALS),),
    ApprovalKind.SKILL_ATTEST: ((0, ApprovalSurface.SKILL_APPROVALS),),
    ApprovalKind.SKILL_PUBLISH: ((0, ApprovalSurface.SKILL_APPROVALS),),
    ApprovalKind.SKILL_SUBMIT: ((0, ApprovalSurface.SKILL_APPROVALS),),
    ApprovalKind.MANAGED_ACTIVATE: ((0, ApprovalSurface.SKILL_APPROVALS),),
    ApprovalKind.RELEASE: ((0, ApprovalSurface.SKILL_APPROVALS),),
    ApprovalKind.OBSIDIAN_WRITE: (
        (0, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.TODO: (
        (0, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
})


def surface_at_policy(
    kind: ApprovalKind,
    policy_version: int,
    transitions: TransitionLedger = _TRANSITIONS,
) -> ApprovalSurface:
    rows = transitions.get(kind)
    if rows is None:
        raise ApprovalSurfaceError(f"approval kind has no transition ledger: {kind}")
    selected: ApprovalSurface | None = None
    for from_version, surface in rows:
        if from_version > policy_version:
            break
        selected = surface
    if selected is None:
        raise ApprovalSurfaceError(
            f"approval kind {kind} has no surface at policy version {policy_version}",
        )
    return selected


def required_surface(kind: ApprovalKind) -> ApprovalSurface:
    match kind:
        case (
            ApprovalKind.MAIL_REPLY
            | ApprovalKind.MAIL_COMPOSE
            | ApprovalKind.BUDGET_MAIL
            | ApprovalKind.PATENT_EXPORT
            | ApprovalKind.REPAIR
            | ApprovalKind.CALENDAR
            | ApprovalKind.COORDINATION
            | ApprovalKind.WIKI
            | ApprovalKind.SKILL_DEPLOY
            | ApprovalKind.SKILL_ATTEST
            | ApprovalKind.SKILL_PUBLISH
            | ApprovalKind.SKILL_SUBMIT
            | ApprovalKind.MANAGED_ACTIVATE
            | ApprovalKind.RELEASE
            | ApprovalKind.OBSIDIAN_WRITE
            | ApprovalKind.TODO
        ):
            return surface_at_policy(kind, POLICY_VERSION)
        case unreachable:
            assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class ChannelFacts:
    channel_type: int
    name: str
    recipient_ids: tuple[str, ...]
    parent_id: str | None = None


class ChannelDirectory(Protocol):
    def owner_dm(self) -> str: ...

    def skill_approvals(self) -> str: ...

    def agent_chat(self) -> str: ...

    def agent_chat_thread(self, kind: ApprovalKind) -> str: ...

    def describe(self, channel_id: str) -> ChannelFacts: ...


@dataclass(frozen=True, slots=True)
class ApprovalBinding:
    kind: ApprovalKind
    surface: ApprovalSurface
    channel_id: str
    policy_version: int

    def __post_init__(self) -> None:
        if (
            not self.channel_id
            or self.channel_id in {"dm", "approvals"}
            or not self.channel_id.isdigit()
        ):
            raise ApprovalSurfaceError(
                f"approval binding requires a concrete Discord channel id: {self.channel_id!r}",
            )


def _resolved_channel_id(
    kind: ApprovalKind,
    surface: ApprovalSurface,
    directory: ChannelDirectory,
    skill_channel_id: str | None,
) -> str:
    try:
        match surface:
            case ApprovalSurface.OWNER_DM:
                return directory.owner_dm()
            case ApprovalSurface.SKILL_APPROVALS:
                if skill_channel_id is not None:
                    return skill_channel_id
                return directory.skill_approvals()
            case ApprovalSurface.AGENT_CHAT_THREAD:
                return directory.agent_chat_thread(kind)
            case unreachable:
                assert_never(unreachable)
    except Exception as error:  # noqa: BLE001 - fail closed; # noqa: BROAD_EXCEPT_OK
        raise ApprovalSurfaceError("approval channel resolution failed") from error


def _validate_channel(
    binding: ApprovalBinding,
    directory: ChannelDirectory,
    owner_id: str,
) -> ApprovalBinding:
    try:
        facts = directory.describe(binding.channel_id)
    except Exception as error:  # noqa: BLE001 - fail closed; # noqa: BROAD_EXCEPT_OK
        raise ApprovalSurfaceError("approval channel facts are unavailable") from error
    match binding.surface:
        case ApprovalSurface.OWNER_DM:
            is_valid = facts.channel_type == 1 and owner_id in facts.recipient_ids
        case ApprovalSurface.SKILL_APPROVALS:
            is_valid = facts.channel_type == 0 and facts.name == "approvals"
        case ApprovalSurface.AGENT_CHAT_THREAD:
            is_valid = facts.channel_type in (11, 12) and (
                facts.parent_id is not None and facts.parent_id == directory.agent_chat()
            )
        case unreachable:
            assert_never(unreachable)
    if not is_valid:
        raise ApprovalSurfaceError(
            f"channel {binding.channel_id} does not match surface {binding.surface}",
        )
    return binding


def resolve_new_binding(
    kind: ApprovalKind,
    directory: ChannelDirectory,
    owner_id: str,
    skill_channel_id: str | None = None,
) -> ApprovalBinding:
    surface = required_surface(kind)
    channel_id = _resolved_channel_id(kind, surface, directory, skill_channel_id)
    binding = ApprovalBinding(kind, surface, channel_id, POLICY_VERSION)
    return _validate_channel(binding, directory, owner_id)


def validate_stored_binding(
    binding: ApprovalBinding,
    directory: ChannelDirectory,
    owner_id: str,
    *,
    transitions: TransitionLedger = _TRANSITIONS,
) -> ApprovalBinding:
    stamped_surface = surface_at_policy(
        binding.kind,
        binding.policy_version,
        transitions,
    )
    if binding.surface is not stamped_surface:
        raise ApprovalSurfaceError(
            f"stored surface {binding.surface} contradicts policy version {binding.policy_version}",
        )
    return _validate_channel(binding, directory, owner_id)


def legacy_binding(
    kind: ApprovalKind,
    raw_channel_id: str | None,
    directory: ChannelDirectory,
    owner_id: str,
) -> ApprovalBinding:
    match raw_channel_id:
        case None | "":
            surface = surface_at_policy(kind, 0)
            channel_id = _resolved_channel_id(kind, surface, directory, None)
        case "dm":
            surface = ApprovalSurface.OWNER_DM
            channel_id = _resolved_channel_id(kind, surface, directory, None)
        case "approvals":
            surface = ApprovalSurface.SKILL_APPROVALS
            channel_id = _resolved_channel_id(kind, surface, directory, None)
        case str() as channel_id:
            surface = surface_at_policy(kind, 0)
        case unreachable:
            assert_never(unreachable)
    binding = ApprovalBinding(kind, surface, channel_id, 0)
    return _validate_channel(binding, directory, owner_id)


def reaction_instruction(
    kind: ApprovalKind,
    surface: ApprovalSurface,
    *,
    name_surface: bool = False,
) -> str:
    del kind
    instruction = "이 메시지에 ✅ 실행 / ⛔ 취소"
    if not name_surface:
        return instruction
    match surface:
        case ApprovalSurface.OWNER_DM:
            surface_name = "소유자 DM"
        case ApprovalSurface.SKILL_APPROVALS:
            surface_name = "개인 서버 #approvals"
        case ApprovalSurface.AGENT_CHAT_THREAD:
            surface_name = "개인 서버 #agent-chat 스레드"
        case unreachable:
            assert_never(unreachable)
    return f"{instruction} ({surface_name})"
