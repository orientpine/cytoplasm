"""Resolve and replay coordination approval bindings through the shared directory."""
from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import ModuleType
from typing import Protocol

import coordinate_io as io
from coordination_pending import PendingConfirmError, PendingConfirmStore

LEASE_DIRNAME = "approval-leases"
JOURNAL_DIRNAME = "posting-journal"


class ApprovalBindingLike(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def surface(self) -> str: ...

    @property
    def channel_id(self) -> str: ...

    @property
    def policy_version(self) -> int: ...


class PendingApproval(Protocol):
    kind: str | None
    surface: str | None
    channel_id: str
    dm_channel_id: str
    policy_version: int | None


class OwnerDmDirectory(Protocol):
    def owner_dm(self) -> str: ...


class RequestPayload(Protocol):
    """What names and places one request's approval thread (the producer's payload)."""

    correlation: str
    draft: Mapping[str, object]
    origin_channel_id: str
    origin_message_id: str


def repo_root() -> Path:
    """The checkout that actually carries ``automation.interop``.

    A mounted release makes ``parents[3]`` point at ``.../releases``. On
    2026-08-18 that produced ``coordination-confirm-watch error: 승인 라이프사이클 모듈 불가``.
    """
    override = os.environ.get("AUTOPHAGY_REPO_ROOT")
    if override:
        return Path(override).expanduser()
    here = Path(__file__).resolve()
    candidates = [*here.parents[2:6], Path("/srv/autophagy-agent-current"), Path("/srv/autophagy-agents")]
    for candidate in candidates:
        if (candidate / "automation" / "interop").is_dir():
            return candidate
    current = Path("/srv/autophagy-agent-current")
    return current if (current / "automation").is_dir() else Path("/srv/autophagy-agents")


def _repo_module(name: str) -> ModuleType:
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        return importlib.import_module(f"automation.interop.{name}")
    except ImportError:
        raise io.CoordinationError(
            f"승인 라이프사이클 모듈 불가 (AUTOPHAGY_REPO_ROOT={root}) — 승인 게시 거부",
            3,
        ) from None


def _surface() -> ModuleType:
    return _repo_module("approval_surface")


def lifecycle() -> ModuleType:
    """Load the shared approval lifecycle through the deployed-repo boundary."""
    return _repo_module("approval_lifecycle")


def _lease_module() -> ModuleType:
    return _repo_module("approval_lease")


def confirm_lease(state_dir: Path | None = None):
    """Create the per-key lease used by coordination approval producers and watchers."""
    root = state_dir or PendingConfirmStore().path.parent
    return _lease_module().FileKeyLease(root / LEASE_DIRNAME)


def posting_journal():
    """Open the append-only coordination posting journal through the shared lease module."""
    return _lease_module().PostingJournal(PendingConfirmStore().path.parent / JOURNAL_DIRNAME)


def approval_directory(owner_id: str | None = None) -> OwnerDmDirectory:
    """Build the shared directory bound to this skill's own credential (SI-7)."""
    directory = _repo_module("approval_directory")
    owner = owner_id or io.interop_config()["owner_id"]
    try:
        token = io.discord_bot_token()
    except io.CoordinationError:
        token = None
    return directory.DiscordChannelDirectory(
        token=token,
        owner_id=owner,
        api=io.api,
        cache_path=PendingConfirmStore().path.parent / "channel.json",
    )


def new_binding(
    owner_id: str | None = None, payload: RequestPayload | None = None
) -> ApprovalBindingLike:
    """Resolve this ONE request's binding — its own thread, labelled as #team labels it.

    스레드 이름은 조율 라벨(correlation, #team 확정 통지가 이미 공개하는 값)이고,
    라벨이 없으면 pending id 로 떨어진다. origin 쌍은 지시 메시지에 스레드를 앵커하는
    데만 쓰이며 승인 표면 자체는 바꾸지 않는다.
    """
    surface = _surface()
    request = surface.RequestThread(
        title="" if payload is None else payload.correlation or str(payload.draft.get("id", "")),
        origin_channel_id="" if payload is None else payload.origin_channel_id,
        origin_message_id="" if payload is None else payload.origin_message_id,
    )
    try:
        return surface.resolve_new_binding(
            surface.ApprovalKind.COORDINATION,
            approval_directory(owner_id),
            owner_id or io.interop_config()["owner_id"],
            request=request,
        )
    except surface.ApprovalSurfaceError as error:
        raise io.CoordinationError(f"승인 표면 해석 실패 — 게시 거부: {error}", 3) from error


def stored_binding(record: Mapping[str, str | int | None]) -> ApprovalBindingLike:
    """Replay a stored binding or migrate its legacy channel sentinel exactly once."""
    surface = _surface()
    kind = surface.ApprovalKind.COORDINATION
    record_kind = record.get("kind")
    if record_kind is not None and record_kind != kind.value:
        raise io.CoordinationError(f"레코드 승인 종류가 coordination이 아님 — 거부: {record_kind!r}", 1)
    channel_id = record.get("channel_id")
    legacy_channel = channel_id if isinstance(channel_id, str) else record.get("dm_channel_id")
    record_surface = record.get("surface")
    version = record.get("policy_version")
    directory = approval_directory()
    try:
        if isinstance(record_surface, str) and type(version) is int and isinstance(channel_id, str):
            return surface.validate_stored_binding(
                surface.ApprovalBinding(
                    kind,
                    surface.ApprovalSurface(record_surface),
                    channel_id,
                    version,
                ),
                directory,
                io.interop_config()["owner_id"],
            )
        return surface.legacy_binding(
            kind,
            legacy_channel if isinstance(legacy_channel, str) else None,
            directory,
            io.interop_config()["owner_id"],
        )
    except (surface.ApprovalSurfaceError, TypeError, ValueError) as error:
        raise io.CoordinationError(f"저장된 승인 표면 검증 실패 — 거부: {error}", 1) from error


def reusable_binding(store: PendingConfirmStore, key: str) -> ApprovalBindingLike | None:
    """The thread an earlier post of this same request already opened, if any.

    한 승인 키는 스레드 하나다 — 같은 해시의 재요청도, 내용이 바뀐 재요청도 첫 게시가 연
    스레드로 돌아간다. 폐지된 표면(옛 DM 바인딩)은 재사용하지 않는다. 읽을 수 없는
    저장소는 여기서 판정하지 않는다 — 파사드가 store-unreadable 로 거부한다.
    """
    try:
        entries: Iterable[PendingApproval] = store.load()
    except PendingConfirmError:
        return None
    surface = _surface()
    required = surface.required_surface(surface.ApprovalKind.COORDINATION).value
    for entry in entries:
        if (
            entry.key == key
            and entry.surface == required
            and entry.channel_id
            and entry.policy_version is not None
        ):
            return binding_for_entry(entry)
    return None


def binding_for_entry(entry: PendingApproval) -> ApprovalBindingLike:
    """Return the authoritative binding for a persisted pending confirmation."""
    return stored_binding(
        {
            "channel_id": entry.channel_id,
            "dm_channel_id": entry.dm_channel_id,
            "kind": entry.kind,
            "policy_version": entry.policy_version,
            "surface": entry.surface,
        }
    )


def channel_for_entry(entry: PendingApproval) -> str:
    """Preserve an old concrete channel while migrating a legacy sentinel."""
    if entry.kind is None and entry.surface is None and entry.policy_version is None:
        if entry.channel_id != "dm":
            return entry.dm_channel_id
    return binding_for_entry(entry).channel_id


def reaction_instruction() -> str:
    """Render the shared surface-neutral owner reaction instruction."""
    surface = _surface()
    kind = surface.ApprovalKind.COORDINATION
    return str(surface.reaction_instruction(kind, surface.required_surface(kind)))
