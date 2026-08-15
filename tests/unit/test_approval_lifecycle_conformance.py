"""Conformance guards for AGENTS.md 「승인 메시지 단일성 규칙」 (AS-1.3).

Ten machine checks that every owner approval in this system — mail, budget,
patent export, repair, drive archive, calendar/coordination/wiki and the skill
supply chain — routes through the shared lifecycle façade, declares an
``ApprovalKind``, persists its binding, and never resolves a Discord surface of
its own. Weakening one of these is an authorization regression, not a test bug.

The inventory each check reads lives in ``approval_conformance_inventory``; the
AST machinery lives in ``approval_conformance_ast``. Both were split out under
AS-1.11 to keep every file under the repo's 250 pure-LOC ceiling — this file now
holds the assertions and nothing else.
"""
from __future__ import annotations

import ast

from approval_conformance_ast import (
    _awaiting_migration,
    _builds_an_approval,
    _deployed_sources,
    _dm_opener_sites,
    _function_names,
    _has_facade_call,
    _inventory_modules,
    _module_is_approval_flow,
    _names_a_surface,
    _posting_callers,
    _reads_flow_env_override,
    _record_fields,
    _references_adapter,
    _resolves_own_channel,
    _scoped_hits,
    _surface_parts,
    _tree,
    _violation,
)
from approval_conformance_inventory import (
    APPROVAL_KINDS,
    APPROVAL_PRODUCERS,
    _ADAPTER_POSTERS,
    _APPROVAL_DIRECTORY,
    _BANNED_RESOLVER_NAMES,
    _BINDING_FIELDS,
    _EXEMPT,
    _HISTORY_ROOTS,
    _LEGACY_MIGRATOR,
    _LIFECYCLE_HOSTS,
    _NON_APPROVAL_DM_SENDERS,
    _PENDING_MIGRATION,
    _POSTING_PRIMITIVE_IMPLEMENTATIONS,
    _RECORD_WRITERS,
    _REPO,
    _SURFACE_NAMING_ALLOWED,
    _SURFACE_SSOT,
)
from automation.interop.approval_surface import ApprovalKind


def test_approval_producers_route_through_shared_lifecycle() -> None:
    failures: list[str] = []
    for surface, adapter in APPROVAL_PRODUCERS.items():
        producer, function = _surface_parts(surface)
        producer_path, adapter_path = _REPO / producer, _REPO / adapter
        if not producer_path.is_file():
            failures.append(_violation(surface, "producer module is missing"))
            continue
        if not adapter_path.is_file():
            failures.append(_violation(surface, f"adapter module is missing: {adapter}"))
            continue
        producer_tree, adapter_tree = _tree(producer), _tree(adapter)
        if function not in _function_names(producer_tree):
            failures.append(_violation(surface, "producer function is missing"))
        if not _references_adapter(producer_tree, adapter, producer):
            failures.append(_violation(surface, f"producer does not reach adapter {adapter}"))
        host = _LIFECYCLE_HOSTS[adapter]
        host_path = _REPO / host
        if not host_path.is_file() or not _has_facade_call(_tree(host)):
            failures.append(_violation(surface, f"adapter lifecycle host lacks request_owner_approval: {host}"))
        if adapter == host and not _has_facade_call(adapter_tree):
            failures.append(_violation(surface, "adapter lacks request_owner_approval"))
    assert not failures, "approval lifecycle conformance failures:\n" + "\n".join(failures)


def test_approval_message_posts_have_a_lifecycle_owner() -> None:
    allowed = frozenset(APPROVAL_PRODUCERS) | _ADAPTER_POSTERS | _POSTING_PRIMITIVE_IMPLEMENTATIONS | frozenset(_EXEMPT)
    failures = [
        _violation(surface, "approval-flow message post is absent from inventory or _EXEMPT")
        for path in _deployed_sources()
        if _module_is_approval_flow(tree := _tree(str(path.relative_to(_REPO))))
        for caller in _posting_callers(tree)
        if (surface := f"{path.relative_to(_REPO)}::{caller}") not in allowed
    ]
    assert not failures, "approval lifecycle conformance failures:\n" + "\n".join(failures)


def test_semantic_action_hashes_exclude_randomness() -> None:
    failures: list[str] = []
    for adapter in frozenset(APPROVAL_PRODUCERS.values()):
        for node in ast.walk(_tree(adapter)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or "action_hash" not in node.name:
                continue
            symbols = {
                nested.id
                for nested in ast.walk(node)
                if isinstance(nested, ast.Name) and nested.id in {"secrets", "uuid", "random"}
            }
            if symbols:
                failures.append(_violation(adapter, f"semantic action hash uses randomness: {sorted(symbols)}"))
    assert not failures, "approval lifecycle conformance failures:\n" + "\n".join(failures)


def test_approval_conformance_exemptions_are_not_stale() -> None:
    failures = [
        _violation(surface, "inventory or exemption function is stale")
        for surface in (*APPROVAL_PRODUCERS, *_EXEMPT, *_ADAPTER_POSTERS, *_POSTING_PRIMITIVE_IMPLEMENTATIONS)
        for module, function in (_surface_parts(surface),)
        if not (_REPO / module).is_file() or function not in _function_names(_tree(module))
    ]
    assert not failures, "approval lifecycle conformance failures:\n" + "\n".join(failures)


def test_every_producer_declares_an_approval_kind() -> None:
    known = {kind.value for kind in ApprovalKind}
    failures: list[str] = []
    for surface in APPROVAL_PRODUCERS:
        kind = APPROVAL_KINDS.get(surface)
        if kind is None:
            failures.append(_violation(surface, "producer declares no ApprovalKind"))
        elif kind not in known:
            failures.append(_violation(surface, f"ApprovalKind {kind!r} is absent from the policy enum"))
    failures += [
        _violation(surface, "APPROVAL_KINDS entry names no producer")
        for surface in APPROVAL_KINDS
        if surface not in APPROVAL_PRODUCERS
    ]
    assert not failures, "approval lifecycle conformance failures:\n" + "\n".join(failures)


def test_producers_do_not_resolve_their_own_channel() -> None:
    failures: list[str] = []
    for module in _inventory_modules():
        if module == _APPROVAL_DIRECTORY or _awaiting_migration(module):
            continue
        hits = _scoped_hits(_tree(module), names=_BANNED_RESOLVER_NAMES, text=_resolves_own_channel)
        failures += [
            _violation(surface, "resolves its own approval surface — only approval_directory.py may")
            for qualname in sorted(hits)
            for surface in (f"{module}::{qualname}",)
            if surface != _LEGACY_MIGRATOR and surface not in _NON_APPROVAL_DM_SENDERS
        ]
    assert not failures, "approval lifecycle conformance failures:\n" + "\n".join(failures)


def test_non_approval_dm_senders_are_not_stale() -> None:
    sites = _dm_opener_sites()
    failures: list[str] = []
    for surface in sorted(_NON_APPROVAL_DM_SENDERS):
        node = sites.get(surface)
        if node is None:
            failures.append(_violation(surface, "non-approval DM sender is stale — renamed or gone"))
        elif _builds_an_approval(node):
            failures.append(_violation(surface, "non-approval DM sender now binds an approval message"))
    assert not failures, "approval lifecycle conformance failures:\n" + "\n".join(failures)


def test_new_pending_records_carry_a_binding() -> None:
    failures: list[str] = []
    for surface, writer in sorted(_RECORD_WRITERS.items()):
        module, _ = _surface_parts(surface)
        if _awaiting_migration(module) or _awaiting_migration(writer):
            continue
        if not (_REPO / writer).is_file():
            failures.append(_violation(surface, f"record writer module is missing: {writer}"))
            continue
        missing = sorted(_BINDING_FIELDS - _record_fields(_tree(writer)))
        if missing:
            failures.append(_violation(surface, f"{writer} pending record carries no binding: {missing}"))
    assert not failures, "approval lifecycle conformance failures:\n" + "\n".join(failures)


def test_runtime_renderers_name_no_physical_surface() -> None:
    failures: list[str] = []
    for path in _deployed_sources():
        relative = str(path.relative_to(_REPO))
        if relative.startswith(_HISTORY_ROOTS) or relative in _SURFACE_SSOT:
            continue
        if _awaiting_migration(relative):
            continue
        for qualname in sorted(_scoped_hits(_tree(relative), names=frozenset(), text=_names_a_surface)):
            surface = f"{relative}::{qualname}"
            if surface not in _SURFACE_NAMING_ALLOWED:
                failures.append(_violation(surface, "runtime string names a physical approval surface"))
    assert not failures, "approval lifecycle conformance failures:\n" + "\n".join(failures)


def test_pending_migration_entries_are_not_stale() -> None:
    failures = [
        _violation(key, f"migration ledger path no longer exists: {part}")
        for key in sorted(_PENDING_MIGRATION)
        for part in key.split("+")
        if not (_REPO / part).exists()
    ]
    # An entry is ALSO stale once the condition it was granted for stops holding.
    # Path existence alone cannot see that, and a spent entry is not cosmetic:
    # `_awaiting_migration()` exempts the file from
    # `test_runtime_renderers_name_no_physical_surface`, so the exact regression the
    # exemption documents would ship unnoticed in the exact file it happened in.
    failures += [
        _violation(key, "migration ledger entry is spent — the file no longer names a surface, so the exemption now only hides future regressions")
        for key in sorted(_PENDING_MIGRATION)
        for part in key.split("+")
        if (_REPO / part).is_file()
        and not _scoped_hits(_tree(part), names=frozenset(), text=_names_a_surface)
    ]
    assert not failures, "approval lifecycle conformance failures:\n" + "\n".join(failures)


def test_no_flow_specific_approvals_env_var_is_read() -> None:
    failures: list[str] = []
    for path in _deployed_sources():
        relative = str(path.relative_to(_REPO))
        hits = _scoped_hits(_tree(relative), names=frozenset(), text=_reads_flow_env_override)
        failures += [
            _violation(f"{relative}::{qualname}", "reads a retired per-flow *_APPROVALS_CHANNEL_ID env override")
            for qualname in sorted(hits)
        ]
    assert not failures, "approval lifecycle conformance failures:\n" + "\n".join(failures)
