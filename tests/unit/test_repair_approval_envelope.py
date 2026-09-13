"""Repair envelope adoption through posting, durable state and the real live probe."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from automation.interop import owner_message
from automation.interop.approval_lifecycle import Probe
from automation.interop.approval_surface import ApprovalKind, ApprovalSurface
from automation.repair import repair_approval_render
from automation.repair.repair_approval_render import ApprovalRenderError, approval_request_content
from automation.repair.repair_ops_approval_gate import probe_pending
from automation.repair.repair_ops_pending import (
    PendingApprovalError, PendingRepairApproval, PendingRepairApprovalStore, PostingOwnerApproval,
)
from automation.repair.repair_ops_reaction_watch import (
    APPROVAL_TTL, ReactionDecision, RepairApprovalWatcher, reaction_decision,
)
from tests.unit.test_repair_approval_watch import FakeRepairCommands
from automation.repair.repair_patch_binding import content_action_hash, load_patch_artifact
from tests.unit.test_repair_pending_v2_schema import _legacy, _v2
from tests.unit.test_repair_single_live_request import FakeApprovalSurface

NOW = datetime(2026, 9, 1, tzinfo=UTC)
TICKET = "t_envelope"
PATCH = (
    "diff --git a/automation/mod.py b/automation/mod.py\n"
    "--- a/automation/mod.py\n+++ b/automation/mod.py\n"
    "@@ -1 +1 @@\n-old\n+PATCH_BODY_SENTINEL_9F3A\n"
)

# Captured once from the shipped v2 posting path with PATCH and fixed inputs.
V2_FALLBACK = (
    "[repair] 승인 요청\n"
    "- ticket: `t_envelope`\n"
    "- action_hash: `sha256:242e2dd46c588d71a22d07e56021db8a5b1ec271910d06485db41b50a5065ed2`\n"
    "- patch_sha256: `34aa66234bf9c6d05e38449a13abb3d1e59cd336dac222f74e0062b3491ba09e`\n"
    "- changed_files: 1 total, +1/-1\n"
    "  - automation/mod.py (+1/-1)\n"
    "- repair_nonce: `nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn`\n"
    "- sandbox: PASS (offline-subset bank + repro GREEN)\n"
    "- patch_body: 비노출 — ops 호스트의 `patch.diff` 에서 확인\n"
    "- cha가 이 메시지에 ✅ 승인 또는 ⛔ 취소 리액션"
)


@dataclass(frozen=True, slots=True)
class Scenario:
    approval: PostingOwnerApproval
    patch: Path
    transport: FakeApprovalSurface


@pytest.fixture
def scenario(tmp_path: Path) -> Scenario:
    patch = tmp_path / "patch.diff"
    patch.write_text(PATCH, encoding="utf-8")
    transport = FakeApprovalSurface()
    approval = PostingOwnerApproval(
        "111", PendingRepairApprovalStore(tmp_path / "pending"), transport,
        now=lambda: NOW, nonce=lambda: "n" * 32,
    )
    return Scenario(approval, patch, transport)


def test_new_card_uses_five_fields_when_content_bound(scenario: Scenario) -> None:
    # Given: a new content-bound repair that passed its sandbox.
    # When: the real posting adapter persists its approval card.
    scenario.approval.permits(TICKET, scenario.patch)
    # Then: one envelope is posted, replayed and accepted by the live exact-text probe.
    content, = scenario.transport.posts
    assert len(content.splitlines()) == 5
    record = scenario.approval.store.get(TICKET)
    assert record is not None
    assert record.render_version == 3
    assert probe_pending(record, "111", scenario.transport) is Probe.BOUND_PENDING
    assert "PATCH_BODY_SENTINEL_9F3A" not in content
    assert record.action_hash in content
    assert record.patch_sha256 is not None and record.patch_sha256 in content
    assert "https://discord.com/" not in content
    assert len(content) <= len(approval_request_content(replace(record, render_version=None)))


@pytest.mark.parametrize("microsecond", [1, 123456, 999999])
def test_v3_fits_v2_budget_when_timestamp_has_microseconds(
    scenario: Scenario, microsecond: int,
) -> None:
    # Given: the production clock's nonzero-microsecond input class.
    approval = replace(scenario.approval, now=lambda: NOW.replace(microsecond=microsecond))
    # When: a one-file patch produces its new approval card.
    approval.permits(TICKET, scenario.patch)
    # Then: exact expiry precision fits the frozen v2 length budget.
    record = approval.store.get(TICKET)
    assert record is not None
    assert len(scenario.transport.posts[0]) <= len(approval_request_content(replace(record, render_version=None)))


def _render_failure(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
    raise owner_message.OwnerMessageError(detail="test.capability")


@pytest.mark.parametrize("failure", ["import", "render"])
def test_new_card_records_fallback_when_capability_fails(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    # Given: fixed patch bytes, clock, nonce and a path independent of the temp root.
    monkeypatch.chdir(scenario.patch.parent)
    # The optional envelope capability is unavailable only during creation.
    with monkeypatch.context() as patch:
        if failure == "import":
            patch.setitem(sys.modules, "automation.interop.owner_message", None)
        else:
            patch.setattr(owner_message, "render", _render_failure)
        # When: the adapter creates a card using its previous renderer.
        scenario.approval.permits(TICKET, Path("patch.diff"))
    # Then: restoring capability never upgrades this already-published body.
    record = scenario.approval.store.get(TICKET)
    assert record is not None and record.render_version == 2
    assert scenario.transport.posts == [V2_FALLBACK]
    assert probe_pending(record, "111", scenario.transport) is Probe.BOUND_PENDING


@pytest.mark.parametrize("failure", ["import", "render"])
def test_stored_v3_is_consumed_without_rendering_when_capability_fails(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    # Given: an approved v3 card, then a missing/broken renderer at probe time.
    scenario.approval.permits(TICKET, scenario.patch)
    record = scenario.approval.store.get(TICKET)
    assert record is not None
    scenario.transport.reactions[(record.message_id, "✅")] = (("111", False),)
    before = dict(scenario.transport.messages)
    assert probe_pending(record, "111", scenario.transport) is Probe.APPROVED
    calls: list[str] = []

    def observed(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        calls.append("owner_message.render")
        raise owner_message.OwnerMessageError(detail="test.capability")

    if failure == "import":
        monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    else:
        monkeypatch.setattr(owner_message, "render", observed)
    # Same stored record, bytes and reaction: optional rendering cannot veto consent.
    assert scenario.approval.permits(TICKET, scenario.patch) is False
    assert scenario.approval.store.get(TICKET) == record
    assert probe_pending(record, "111", scenario.transport) is Probe.APPROVED
    assert reaction_decision(record, "111", scenario.transport) is ReactionDecision.APPROVED
    commands = FakeRepairCommands()
    audit = scenario.approval.store.root.parent / "audit.jsonl"
    RepairApprovalWatcher(
        scenario.approval.store, scenario.transport, commands, "111", audit, lambda: NOW,
    ).run_once()
    assert commands.applied == [record]
    assert scenario.approval.store.get(TICKET) is None
    assert json.loads(audit.read_text(encoding="utf-8"))["result"]["status"] == "approved"
    assert calls == []
    assert scenario.transport.messages == before
    assert scenario.transport.calls == [f"post:{record.message_id}"]
    print(f"{failure}: same bytes/reaction -> approved consumed; render_calls={calls}; no delete/repost")


def test_post_and_commit_share_one_clock_sample_when_time_advances(scenario: Scenario) -> None:
    # Given: a clock that advances every time it is called (no wall-clock dependence).
    samples: list[datetime] = []

    def clock() -> datetime:
        value = NOW + timedelta(seconds=len(samples))
        samples.append(value)
        return value

    approval = replace(scenario.approval, now=clock)
    # When: posting freezes a timestamp and commit persists it.
    approval.permits(TICKET, scenario.patch)
    # Then: the shown expiry and stored record reproduce exactly.
    record = approval.store.get(TICKET)
    assert record is not None
    assert samples == [NOW]
    assert probe_pending(record, "111", scenario.transport) is Probe.BOUND_PENDING


def test_v3_instruction_is_direct_when_legacy_formatter_is_unusable(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the old shared formatter cannot be consulted by a new renderer.
    def legacy_instruction(kind: ApprovalKind, surface: ApprovalSurface) -> str:
        raise AssertionError("legacy formatter must not build the v3 instruction")

    captured: list[owner_message.OwnerMessage] = []
    real_render = owner_message.render

    def capture(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        captured.append(message)
        return real_render(message, destination=destination)

    monkeypatch.setattr(repair_approval_render, "reaction_instruction", legacy_instruction)
    monkeypatch.setattr(owner_message, "render", capture)
    # When: posting constructs the new card, without a shared string rewrite.
    scenario.approval.permits(TICKET, scenario.patch)
    # Then: the actual envelope carries self-local reaction, TTL and cancellation.
    message, = captured
    assert message.location == owner_message.Ref(scope="self")
    assert message.owner.verb == "react" and message.owner.target == message.location
    assert message.owner.argument is not None
    assert all(emoji in message.owner.argument for emoji in ("✅", "⛔"))
    assert isinstance(message.detail, owner_message.Approval)
    assert message.detail.expires_at == NOW + APPROVAL_TTL
    assert message.detail.cancel_effect
    assert message.subject_key == TICKET


@pytest.mark.parametrize("version", [0, 1, 4, True, "3", 3.0, [], {}])
def test_stored_record_is_refused_when_render_version_is_unknown(
    scenario: Scenario, version: int | bool | str | float | list[str] | dict[str, str],
) -> None:
    # Given: a stored card with malformed render metadata but unchanged binding.
    scenario.approval.permits(TICKET, scenario.patch)
    path, = scenario.approval.store.root.glob("*.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["render_version"] = version
    path.write_text(json.dumps(payload), encoding="utf-8")
    # When / Then: storage refuses before the gate can trust or replace this card.
    with pytest.raises(PendingApprovalError):
        scenario.approval.store.get(TICKET)


def test_binding_hash_is_unchanged_when_render_version_changes(scenario: Scenario) -> None:
    # Given: the same patch artifact for both old and new card versions.
    artifact = load_patch_artifact(scenario.patch)
    expected = content_action_hash(TICKET, scenario.patch.name, artifact.patch_sha256, artifact.changes)
    # When: a new envelope card is posted.
    scenario.approval.permits(TICKET, scenario.patch)
    # Then: the pre-existing binding schema still derives its exact action hash.
    record = scenario.approval.store.get(TICKET)
    assert record is not None
    assert record.content_binding_version == 2 and record.action_hash == expected
    scenario.approval.store.save(replace(record, render_version=2))
    old = scenario.approval.store.get(TICKET)
    assert old is not None and old.action_hash == expected


def test_no_effects_when_neither_card_version_can_render(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: even the old version cannot fit, so no consentable card exists.
    monkeypatch.setattr(repair_approval_render, "MAX_APPROVAL_CONTENT_CHARS", 10)
    # When / Then: fail before posting, reactions, pending writes or journal reserve.
    with pytest.raises(ApprovalRenderError):
        scenario.approval.permits(TICKET, scenario.patch)
    assert scenario.transport.calls == [] and scenario.transport.added == []
    assert not scenario.approval.store.root.exists()
    assert not (scenario.approval.store.root.parent / "repair-approval-journal").exists()


@pytest.mark.parametrize("legacy", [_legacy(), _v2()])
def test_legacy_instruction_keeps_shared_formatter_behavior_when_replayed(
    monkeypatch: pytest.MonkeyPatch, legacy: PendingRepairApproval,
) -> None:
    # Given: the frozen renderers retain their historical formatter dependency.
    monkeypatch.setattr(repair_approval_render, "reaction_instruction", lambda kind, surface: "LEGACY_SENTINEL")
    # When: the old stored version is replayed.
    content = approval_request_content(legacy)
    # Then: v1/v2 still consume that dependency; v3's direct wording did not alter it.
    assert "LEGACY_SENTINEL" in content


def test_probe_refuses_modified_text_when_owner_has_approved(scenario: Scenario) -> None:
    # Given: a stored v3 card with a valid owner reaction but a one-character edit.
    scenario.approval.permits(TICKET, scenario.patch)
    record = scenario.approval.store.get(TICKET)
    assert record is not None
    scenario.transport.messages[record.message_id] += "X"
    scenario.transport.reactions[(record.message_id, "✅")] = (("111", False),)
    # When: the real live probe checks the bound content before reactions.
    result = probe_pending(record, "111", scenario.transport)
    # Then: it refuses the edit rather than treating owner approval as enough.
    assert result is Probe.BINDING_MISMATCH


def test_existing_v2_is_preserved_when_renderer_upgrades(scenario: Scenario) -> None:
    # Given: an already-posted card with the old, absent render metadata.
    scenario.approval.permits(TICKET, scenario.patch)
    record = scenario.approval.store.get(TICKET)
    assert record is not None
    legacy = replace(record, render_version=None, content_sha256=None)
    scenario.approval.store.save(legacy)
    scenario.transport.messages[record.message_id] = approval_request_content(legacy)
    # When: the upgraded producer sees the same patch again.
    scenario.approval.permits(TICKET, scenario.patch)
    # Then: the old card remains byte-identical; no post/delete or metadata upgrade.
    assert scenario.approval.store.get(TICKET) == legacy
    assert scenario.transport.calls == [f"post:{record.message_id}"]
    assert probe_pending(legacy, "111", scenario.transport) is Probe.BOUND_PENDING
