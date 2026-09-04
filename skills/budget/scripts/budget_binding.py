"""Where one budget approval lives — resolved once, then replayed.

A new request asks the shared directory for its binding exactly once; the draft
record persists that answer, and every later read, reaction poll and delete
replays the STORED binding instead of resolving again. A record written before
this schema carries no binding and drains through the legacy migrator, so the
historical approval message stays consumable and is never retargeted.

This is the only budget module that names an approval surface: the transport
(``budget_confirm``) and the record store (``budget_gate``) consume a binding
they are handed. The repo modules are reached lazily through
``AUTOPHAGY_REPO_ROOT`` (``budget_gate.repo_module``) because a deployed skill
cannot import ``automation.*`` at module scope, and an unreachable repo refuses
the request instead of falling back to an unbound surface.
"""
from __future__ import annotations

from collections.abc import Iterable
from types import ModuleType
from typing import Protocol

import budget_confirm
import budget_gate


class _Directory(Protocol):
    def owner_dm(self) -> str: ...


def _surface() -> ModuleType:
    return budget_gate.repo_module("approval_surface")


def approval_directory() -> _Directory:
    """The one approval-surface resolver, bound to THIS bot's identity (SI-7)."""
    return budget_gate.repo_module("approval_directory").DiscordChannelDirectory(
        token=budget_confirm.bot_token(),
        owner_id=budget_confirm.owner_id(),
        api=budget_confirm._api,
        cache_path=budget_gate.gate_dir() / "channel.json",
    )


def request_spec(record: dict):
    """This draft's own approval-thread spec: 제목만 실린다.

    스레드 이름은 발신할 메일의 제목이다 — 금액·잔액은 승인 카드 안에서만 마스킹된
    형태로 보이고 스레드 이름에는 절대 들어가지 않는다. origin 쌍은 소유자의 지시
    메시지이며, 그 메시지가 승인 표면과 같은 채널에 있을 때만 스레드를 거기에 앵커한다.
    """
    surface = _surface()
    return surface.RequestThread(
        title=str(record.get("subject") or ""),
        origin_channel_id=str(record.get("origin_channel_id") or ""),
        origin_message_id=str(record.get("origin_message_id") or ""),
    )


def new_binding(record: dict) -> budget_gate.ApprovalBindingLike:
    """Resolve the surface for a NEW post — the only surface resolution in this flow.

    요청 하나가 스레드 하나를 연다: 승인 카드·리마인더·결과 통지가 한 스레드에서
    완결되도록 이 초안의 요청 스펙을 정책에 넘긴다.
    """
    surface = _surface()
    try:
        return surface.resolve_new_binding(
            surface.ApprovalKind.BUDGET_MAIL,
            approval_directory(),
            budget_confirm.owner_id(),
            request=request_spec(record),
        )
    except surface.ApprovalSurfaceError as error:
        raise budget_gate.GateError(f"승인 표면 해석 실패 — 게시 거부: {error}", 3) from error


def stored_binding(record: dict) -> budget_gate.ApprovalBindingLike:
    """The binding this record's message lives on — read from the record, never re-resolved."""
    surface = _surface()
    kind = surface.ApprovalKind.BUDGET_MAIL
    record_kind = record.get("kind")
    if record_kind is not None and record_kind != kind.value:
        raise budget_gate.GateError(f"레코드 승인 종류가 budget이 아님 — 거부: {record_kind!r}", 1)
    channel_id, record_surface = record.get("channel_id"), record.get("surface")
    version = record.get("policy_version")
    directory, owner = approval_directory(), budget_confirm.owner_id()
    bound = isinstance(record_surface, str) and type(version) is int
    try:
        if bound:
            return surface.validate_stored_binding(
                surface.ApprovalBinding(
                    kind, surface.ApprovalSurface(record_surface), str(channel_id), version
                ),
                directory,
                owner,
            )
        legacy = channel_id if isinstance(channel_id, str) else None
        return surface.legacy_binding(kind, legacy, directory, owner)
    except (surface.ApprovalSurfaceError, ValueError) as error:
        raise budget_gate.GateError(f"저장된 승인 표면 검증 실패 — 거부: {error}", 1) from error


def persisted_channel_id(record: dict) -> str | None:
    """The channel this record was ACTUALLY posted to, read raw — no resolve, no network.

    An injected confirm only compares against where the message already lives, so it
    must never resolve a new binding: doing so both reaches Discord and would invent a
    channel for a record that was never posted.
    """
    channel_id, surface = record.get("channel_id"), record.get("surface")
    version = record.get("policy_version")
    if (
        isinstance(channel_id, str)
        and channel_id.isdigit()
        and isinstance(surface, str)
        and surface
        and type(version) is int
        and version >= 0
    ):
        return channel_id
    return None


def reused_binding(outstanding: Iterable[object]) -> budget_gate.ApprovalBindingLike | None:
    """The request thread a LIVE request of the same승인 키 already opened, if any.

    파사드는 스레드가 열린 뒤에야 PENDING(같은 해시)·supersede(내용 변경)를 판정하므로,
    재요청마다 빈 스레드가 하나씩 남았다. 살아 있는 요청이 없으면 표면을 조회하지도 않는다.
    """
    candidates = tuple(outstanding)
    if not candidates:
        return None
    surface = _surface()
    return surface.reuse_request_thread(
        surface.ApprovalKind.BUDGET_MAIL,
        candidates,
        approval_directory(),
        budget_confirm.owner_id(),
    )


def binding_for(
    record: dict, outstanding: Iterable[object] = ()
) -> budget_gate.ApprovalBindingLike:
    """A stored binding always wins; a never-posted record reuses this key's live thread."""
    channel_id = record.get("channel_id")
    if isinstance(channel_id, str) and channel_id:
        return stored_binding(record)
    return reused_binding(outstanding) or new_binding(record)


def reaction_instruction(record: dict, *, name_surface: bool = False) -> str:
    """The owner-facing reaction line for THIS record's surface — never hardcoded.

    A record that already carries a binding is described by ITS stored surface; a
    draft that has not been posted yet is described by current policy.
    """
    surface_module = _surface()
    kind = surface_module.ApprovalKind.BUDGET_MAIL
    stored = record.get("surface")
    surface = (
        surface_module.ApprovalSurface(stored)
        if isinstance(stored, str)
        else surface_module.required_surface(kind)
    )
    return str(surface_module.reaction_instruction(kind, surface, name_surface=name_surface))
