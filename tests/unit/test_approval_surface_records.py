"""Characterization locks for what an approval record PERSISTS (AS-0.2, items
(c)-(h)), split out of ``test_approval_surface_characterization.py`` under
AS-1.11 so every file stays under the 250 pure-LOC ceiling.

The sibling file pins WHERE each flow's intent gets its channel from. This one
pins what survives the post: the annotated field order of every pending record,
that each ends in the whole binding — kind AND surface AND channel AND the policy
version it was stamped at — and that the skill supply chain, which persists no
channel at all today, resolves the guild surface through the shared directory and
still routes exactly where it did before.

Same rule as the sibling: every assertion pins CURRENT behaviour, so a later AS
task moving a flow has to update this file deliberately.
"""
from __future__ import annotations

import pytest

# Load-bearing import order: the fixtures module puts the repo root on ``sys.path``.
from approval_characterization_fixtures import _BINDING_FIELDS  # pyright: ignore[reportImplicitRelativeImport]
from approval_characterization_ast import (  # pyright: ignore[reportImplicitRelativeImport]
    _annotated_fields,
    _called_names,
    _definition,
    _dict_literal_keys,
    _keyword_bindings,
    _returned_dict_keys,
)
from approval_conformance_inventory import _REPO  # pyright: ignore[reportImplicitRelativeImport]

from automation.interop import approval_surface
from automation.interop.approval_surface import (
    POLICY_VERSION,
    ApprovalSurface,
    surface_at_policy,
)
from automation.skill_gate_surface import SUPPLY_CHAIN_KINDS


# ------------------------------------------------- persisted record field sets (c), (d), (e), (f)
@pytest.mark.parametrize(
    ("relative", "class_name", "writer", "fields", "nested"),
    (
        (
            "skills/patent-prep/scripts/patent_export_manifest.py",
            "Manifest",
            "write_manifest",
            ("slug", "plaintext_sha256", "dest_folder_id", "mode", "expiry_ts", "nonce", "state",
             "message_id", "created_ts", "approval_ts", "kind", "surface", "channel_id",
             "policy_version"),
            (),
        ),
        (
            "automation/repair/repair_ops_pending.py",
            "PendingRepairApproval",
            "save",
            # RTS-4: the four content-binding fields sit BEFORE the approval binding,
            # because the binding must remain the last four fields of every record.
            ("ticket_id", "patch_name", "action_hash", "nonce", "message_id", "created_at",
             "content_binding_version", "patch_sha256", "changes", "patch_source_path",
             "kind", "surface", "channel_id", "policy_version"),
            (),
        ),
    ),
    ids=("patent-manifest", "repair-pending"),
)
def test_records_that_persist_a_resolved_binding(
    relative: str, class_name: str, writer: str, fields: tuple[str, ...],
    nested: tuple[str, ...],
) -> None:
    # Given / When: the persisted field set and the payload its writer serialises;
    # ``nested`` names the keys of the per-row dicts built INSIDE that payload.
    persisted = _annotated_fields(relative, class_name)

    # Then: it is exactly the known set, it ends in the whole binding — surface AND
    # channel AND the policy version it was stamped at — and the writer round-trips
    # every one of them, so a later read replays the binding instead of resolving.
    assert persisted == fields
    assert persisted[-len(_BINDING_FIELDS):] == _BINDING_FIELDS
    assert _dict_literal_keys(relative, writer) == frozenset(fields) | frozenset(nested)


@pytest.mark.parametrize(
    ("relative", "approval", "fields"),
    (
        (
            "skills/calendar/scripts/calendar_pending.py",
            "skills/calendar/scripts/calendar_approval.py",
            ("draft_id", "sha256", "dm_channel_id", "dm_message_id", "created", "key",
             "kind", "surface", "channel_id", "policy_version"),
        ),
        (
            "skills/coordination/scripts/coordination_pending.py",
            "skills/coordination/scripts/coordination_approval.py",
            ("draft_id", "sha256", "dm_channel_id", "dm_message_id", "slot", "summary",
             "correlation", "duration_min", "created", "key",
             "kind", "surface", "channel_id", "policy_version"),
        ),
    ),
    ids=("calendar", "coordination"),
)
def test_pending_confirm_records_make_the_binding_the_only_channel_source(
    relative: str, approval: str, fields: tuple[str, ...]
) -> None:
    # Given / When: the persisted field set, and every channel its writer sets.
    persisted = _annotated_fields(relative, "PendingConfirm")
    written = frozenset(
        source for name, source in _keyword_bindings(approval, "commit") if "channel" in name
    )

    # Then: the record now ends in the whole binding, and the older `dm_channel_id`
    # column is a MIRROR of it — ONE expression fills both, so no read can pick the
    # disagreeing one and delete a superseded draft out of the wrong channel (SI-5).
    assert persisted == fields
    assert persisted[-len(_BINDING_FIELDS):] == _BINDING_FIELDS
    assert written == frozenset({"binding.channel_id"})


# -------------------------------------------------------- skill supply chain (h)
def test_skill_supply_chain_resolves_the_guild_surface_through_the_shared_directory() -> None:
    # Given: the two supply-chain gate modules and the surface module they share.
    gate = (_REPO / "automation/skill_gate.py").read_text(encoding="utf-8")
    publish = (_REPO / "automation/skill_gate_publish.py").read_text(encoding="utf-8")
    surface = (_REPO / "automation/skill_gate_surface.py").read_text(encoding="utf-8")

    # When / Then: the `deploy_approvals_channel_id` ladder both gates used to walk
    # is gone. Each now declares a KIND and lets the shared directory answer where;
    # the operator's pin survives as one key, read in one module, and is handed to
    # the directory as a supplied channel it still verifies before use.
    assert '.get("deploy_approvals_channel_id")' not in gate
    assert "_config_deploy_channel_id" not in gate
    assert "skill_gate._approvals_channel_id" not in publish
    assert '_PINNED_CHANNEL_KEY: Final = "deploy_approvals_channel_id"' in surface
    assert _called_names(_definition("automation/skill_gate.py", "_deploy_bindings")) == frozenset(
        {"_identity", "deploy_kind", "surface_for"}
    )
    assert _called_names(
        _definition("automation/skill_gate_publish.py", "_publish_bindings")
    ) == frozenset({"_identity", "surface_for"})
    assert _called_names(
        _definition("automation/skill_gate_surface.py", "GateIdentity.directory")
    ) == frozenset({"DiscordChannelDirectory", "owner_id"})
    assert _called_names(
        _definition("automation/skill_gate_surface.py", "SupplyChainSurface.new")
    ) == frozenset({"resolve_new_binding"})

    # And the ROUTING did not move with it: the peer bot replies beside the deploy
    # request, and a DM between the owner and one bot cannot carry another bot's
    # message — so every supply-chain kind stays on the guild surface, with exactly
    # ONE transition each, and cannot be swept along by a later flip.
    ledger = approval_surface._TRANSITIONS  # noqa: SLF001
    assert {kind: ledger[kind] for kind in SUPPLY_CHAIN_KINDS} == {
        kind: ((0, ApprovalSurface.SKILL_APPROVALS),) for kind in SUPPLY_CHAIN_KINDS
    }
    assert [surface_at_policy(kind, POLICY_VERSION) for kind in SUPPLY_CHAIN_KINDS] == [
        ApprovalSurface.SKILL_APPROVALS
    ] * len(SUPPLY_CHAIN_KINDS)


@pytest.mark.parametrize(
    ("class_name", "fields", "record_keys"),
    (
        (
            "DeploySpec",
            (
                "skill",
                "digest",
                "deploy_nonce",
                "review_status",
                "provenance",
                "binding",
                "peer_attest_mode",
                "peer_status",
            ),
            ("deploy_nonce", "hash", "message_id", "action_hash", "approval_action", "approval_destination"),
        ),
        (
            "PublishSpec",
            ("skill", "digest", "manifest_hash", "tag", "publish_nonce", "binding"),
            ("hash", "manifest_hash", "message_id", "publish_nonce", "tag", "action_hash", "approval_action", "approval_destination"),
        ),
    ),
    ids=("skill-deploy", "skill-publish"),
)
def test_skill_gate_record_specs_persist_no_channel_today(
    class_name: str, fields: tuple[str, ...], record_keys: tuple[str, ...]
) -> None:
    # Given: the spec that owns one supply-chain approval record.
    relative = "automation/skill_gate_specs.py"

    # When / Then: neither the spec nor the record it writes carries a channel.
    assert _annotated_fields(relative, class_name) == fields
    assert _returned_dict_keys(relative, f"{class_name}.new_record") == frozenset(record_keys)
