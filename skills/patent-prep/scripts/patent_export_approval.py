"""Patent-export adapter for the shared owner-approval lifecycle."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Protocol, assert_never
from urllib.error import HTTPError, URLError

from . import patent_export_binding as export_binding
from . import patent_export_gate as export_gate
from . import patent_export_manifest as manifest
from .patent_export import render_approval
from .patent_storage import require_slug

if TYPE_CHECKING:
    from automation.interop.approval_lease import ApprovalLease, PostingJournal
    from automation.interop.approval_lifecycle import (
        ApprovalIntent,
        ApprovalRequest,
        PostedApproval,
        Probe,
    )
    from automation.interop.approval_surface import ApprovalBinding

_KEY_PREFIX = "patent:"
_TRANSPORT_ERRORS = (
    export_gate.ExportGateError,
    HTTPError,
    URLError,
    OSError,
    json.JSONDecodeError,
    KeyError,
    TypeError,
)


class AuthorizingFields(Protocol):
    plaintext_sha256: str
    dest_folder_id: str
    mode: str
    expiry_ts: int


@dataclass(frozen=True, slots=True)
class PatentApprovalPayload:
    slug: str
    plaintext_sha256: str
    dest_folder_id: str
    mode: str
    expiry_ts: int
    created_ts: int

    def pending(self, nonce: str, binding: ApprovalBinding) -> manifest.Manifest:
        return manifest.Manifest(
            slug=self.slug,
            plaintext_sha256=self.plaintext_sha256,
            dest_folder_id=self.dest_folder_id,
            mode=self.mode,
            expiry_ts=self.expiry_ts,
            nonce=nonce,
            state=manifest.State.PENDING,
            message_id=None,
            created_ts=self.created_ts,
            approval_ts=None,
            kind=str(binding.kind),
            surface=str(binding.surface),
            channel_id=binding.channel_id,
            policy_version=binding.policy_version,
        )


def repo_root() -> Path:
    return export_binding.repo_root()


def _repo_module(name: str) -> ModuleType:
    return export_binding.repo_module(name)


def lifecycle() -> ModuleType:
    return _repo_module("approval_lifecycle")


def _lease_module() -> ModuleType:
    return _repo_module("approval_lease")


def approval_key(slug: str) -> str:
    return f"{_KEY_PREFIX}{require_slug(slug)}"


def _key_slug(key: str) -> str:
    if not key.startswith(_KEY_PREFIX):
        raise export_gate.ExportGateError("invalid patent approval key", 3)
    return require_slug(key.removeprefix(_KEY_PREFIX))


def semantic_action_hash(fields: AuthorizingFields) -> str:
    encoded = json.dumps(
        {
            "dest_folder_id": fields.dest_folder_id,
            "expiry_ts": fields.expiry_ts,
            "mode": fields.mode,
            "plaintext_sha256": fields.plaintext_sha256,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _manifest_binding(entry: manifest.Manifest) -> tuple[str, str, str]:
    return entry.slug, entry.nonce, entry.plaintext_sha256


@dataclass(frozen=True, slots=True)
class PatentManifestLease:
    @contextmanager
    def hold(self, key: str) -> Iterator[bool]:
        slug = _key_slug(key)
        with ExitStack() as stack:
            try:
                stack.enter_context(manifest.lock(slug))
            except manifest.ManifestError:
                yield False
                return
            yield True


def confirm_lease() -> ApprovalLease:
    return PatentManifestLease()


def posting_journal() -> PostingJournal:
    return _lease_module().PostingJournal(manifest._export_root() / "posting-journal")


def supersede(slug: str, nonce: str, plaintext_sha256: str) -> bool:
    current = manifest.load_manifest(slug)
    if current.state is not manifest.State.PENDING or _manifest_binding(current) != (
        slug,
        nonce,
        plaintext_sha256,
    ):
        return False
    manifest.write_manifest(replace(current, state=manifest.State.CANCELLED))
    return True


class PatentApprovalGate:
    """Stateful per-call adapter retaining exact manifest bindings while its lease is held."""

    __slots__ = ("payload", "binding", "channel_id", "_fresh", "_observed")

    def __init__(self, payload: PatentApprovalPayload, binding: ApprovalBinding) -> None:
        self.payload = payload
        self.binding = binding
        self.channel_id = binding.channel_id
        self._fresh: manifest.Manifest | None = None
        self._observed: dict[str, manifest.Manifest] = {}

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        facade = lifecycle()
        if key != approval_key(self.payload.slug):
            raise facade.ApprovalRecordsError(key)
        path = manifest.manifest_path(self.payload.slug)
        try:
            path.stat()
        except FileNotFoundError:
            return ()
        except OSError as error:
            raise facade.ApprovalRecordsError(str(path)) from error
        try:
            current = manifest.load_manifest(self.payload.slug)
        except (manifest.ManifestError, OSError) as error:
            raise facade.ApprovalRecordsError(str(path)) from error
        match current.state:
            case manifest.State.CANCELLED | manifest.State.CONSUMED:
                return ()
            case manifest.State.PENDING | manifest.State.APPROVED:
                if current.message_id is None:
                    raise facade.ApprovalRecordsError(str(path))
            case unreachable:
                assert_never(unreachable)
        try:
            bound = export_binding.stored_binding(current)
        except export_gate.ExportGateError as error:
            raise facade.ApprovalRecordsError(str(path)) from error
        request = facade.ApprovalRequest(
            key=key,
            action_hash=semantic_action_hash(current),
            message_id=current.message_id,
            channel_id=bound.channel_id,
            created_at=str(current.created_ts),
        )
        self._observed[request.message_id] = current
        return (request,)

    def probe(self, request: ApprovalRequest) -> Probe:
        state = lifecycle().Probe
        current = self._load_for_probe(request)
        match current.state:
            case manifest.State.APPROVED:
                return state.APPROVED
            case manifest.State.CANCELLED:
                return state.CANCELLED
            case manifest.State.CONSUMED:
                return state.BINDING_MISMATCH
            case manifest.State.PENDING:
                if not self._matches_observed(request, current):
                    return state.BINDING_MISMATCH
            case unreachable:
                assert_never(unreachable)
        try:
            content = export_gate.approval_message_content(request.channel_id, request.message_id)
            if content is None:
                return state.MISSING
            if not export_gate.approval_binding_matches(current, content):
                return state.BINDING_MISMATCH
            reaction = export_gate.reaction_state(current)
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        match reaction:
            case export_gate.CANCEL_EMOJI:
                return state.CANCELLED
            case export_gate.APPROVE_EMOJI:
                return state.APPROVED
            case None:
                return state.BOUND_PENDING
            case unreachable:
                assert_never(unreachable)

    def delete(self, request: ApprovalRequest) -> None:
        try:
            export_gate.delete_approval_request(request.channel_id, request.message_id)
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error

    def drop(self, request: ApprovalRequest) -> None:
        observed = self._observed.get(request.message_id)
        if observed is None or not self._matches_observed(request, observed):
            raise export_gate.ExportGateError("patent manifest binding changed; supersede refused", 3)
        try:
            changed = supersede(observed.slug, observed.nonce, observed.plaintext_sha256)
        except (manifest.ManifestError, OSError) as error:
            raise export_gate.ExportGateError("patent manifest supersede failed", 3) from error
        if not changed:
            raise export_gate.ExportGateError("patent manifest is no longer pending", 3)

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        fresh = self.payload.pending(manifest.mint_nonce(), self.binding)
        self._fresh = fresh
        try:
            message_id = export_gate.post_approval_request(self.channel_id, render_approval(fresh))
            export_gate.add_reaction(self.channel_id, message_id, export_gate.APPROVE_EMOJI)
            export_gate.add_reaction(self.channel_id, message_id, export_gate.CANCEL_EMOJI)
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        return lifecycle().PostedApproval(message_id=message_id, channel_id=intent.channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        del created_at
        fresh = self._fresh
        if fresh is None or semantic_action_hash(fresh) != intent.action_hash:
            raise lifecycle().ApprovalRecordsError("patent approval payload unavailable")
        committed = replace(fresh, message_id=posted.message_id)
        manifest.write_manifest(committed)
        self._fresh = committed

    def result(self, request: ApprovalRequest | None = None) -> manifest.Manifest | None:
        if request is None:
            return self._fresh
        return self._observed.get(request.message_id)

    def _load_for_probe(self, request: ApprovalRequest) -> manifest.Manifest:
        try:
            return manifest.load_manifest(_key_slug(request.key))
        except (manifest.ManifestError, OSError, export_gate.ExportGateError) as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error

    def _matches_observed(self, request: ApprovalRequest, current: manifest.Manifest) -> bool:
        observed = self._observed.get(request.message_id)
        if observed is None:
            return False
        return (
            _manifest_binding(current) == _manifest_binding(observed)
            and current.message_id == request.message_id
            and semantic_action_hash(current) == request.action_hash
        )
