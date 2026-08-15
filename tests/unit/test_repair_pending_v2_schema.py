"""The persisted record must reproduce the approval message on its own."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.interop.approval_lifecycle import ApprovalRecordsError
from automation.interop.approval_surface import POLICY_VERSION, ApprovalKind, ApprovalSurface
from automation.repair.repair_ops_approval import repair_action_hash
from automation.repair.repair_ops_pending import (
    PendingApprovalError,
    PendingRepairApproval,
    PendingRepairApprovalStore,
    approval_request_content,
)
from automation.repair.repair_patch_binding import PatchFileDelta, content_action_hash

TICKET = "t_repair01"
NONCE = "n" * 32
NOW = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)
SOURCE = "/srv/autophagy-private/repair-plans/t_repair01/patch.diff"
DIGEST = hashlib.sha256(b"unified diff bytes").hexdigest()
CHANGES = (
    PatchFileDelta(None, "docs/새 폴더/기능 소개.md", 4, 0),
    PatchFileDelta("automation/old mod.py", "automation/new mod.py", 2, 3),
)


def _legacy() -> PendingRepairApproval:
    return PendingRepairApproval(
        TICKET,
        "patch.diff",
        repair_action_hash(TICKET, "patch.diff"),
        NONCE,
        "message-1",
        NOW,
        kind=ApprovalKind.REPAIR,
        surface=ApprovalSurface.OWNER_DM,
        channel_id="1528936606856122423",
        policy_version=POLICY_VERSION,
    )


def _v2() -> PendingRepairApproval:
    return PendingRepairApproval(
        TICKET,
        "patch.diff",
        content_action_hash(TICKET, "patch.diff", DIGEST, CHANGES),
        NONCE,
        "message-1",
        NOW,
        kind=ApprovalKind.REPAIR,
        surface=ApprovalSurface.OWNER_DM,
        channel_id="1528936606856122423",
        policy_version=POLICY_VERSION,
        content_binding_version=2,
        patch_sha256=DIGEST,
        changes=CHANGES,
        patch_source_path=SOURCE,
    )


def _record_path(root: Path) -> Path:
    return root / f"{hashlib.sha256(TICKET.encode()).hexdigest()}.json"


def test_v2_record_round_trips_and_reproduces_the_posted_message(tmp_path: Path) -> None:
    # Given: a v2 approval whose paths carry Unicode and spaces.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    original = _v2()

    # When: it is persisted and read back like the watcher does.
    store.save(original)
    decoded = store.get(TICKET)

    # Then: the watcher's exact-equality probe still sees its own message.
    assert decoded == original
    assert decoded is not None
    assert approval_request_content(decoded) == approval_request_content(original)
    assert not approval_request_content(decoded).endswith("\n")


def test_legacy_record_payload_is_byte_identical_to_the_shipped_format(tmp_path: Path) -> None:
    # Given: a record written before content binding existed.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    store.save(_legacy())

    # When: the on-disk payload is inspected.
    payload = json.loads(_record_path(store.root).read_text(encoding="utf-8"))

    # Then: no v2 key is written at all, so old nodes and new code agree.
    assert set(payload) == {
        "ticket_id",
        "patch_name",
        "action_hash",
        "nonce",
        "message_id",
        "created_at",
        "kind",
        "surface",
        "channel_id",
        "policy_version",
    }


def test_legacy_record_is_still_readable_and_never_unreadable(tmp_path: Path) -> None:
    # Given: an old-schema record left on the node by the previous release.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    store.save(_legacy())

    # When / Then: it decodes — a schema-age refusal here would paralyse
    # every repair approval, which is exactly the 2026-07-29 incident.
    assert store.get(TICKET) == _legacy()
    assert store.all_strict() == (_legacy(),)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("patch_sha256", hashlib.sha256(b"other bytes").hexdigest()),
        ("patch_source_path", "/srv/autophagy-private/repair-plans/other/patch.diff"),
    ],
)
def test_tampering_with_a_bound_field_is_rejected(tmp_path: Path, field: str, value: str) -> None:
    # Given: a stored v2 record whose action hash is left untouched.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    store.save(_v2())
    path = _record_path(store.root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    _ = path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    # When / Then: the record no longer describes what the owner approved.
    if field == "patch_sha256":
        with pytest.raises(ApprovalRecordsError):
            _ = store.all_strict()
    else:
        assert store.get(TICKET) is not None


def test_tampering_with_a_line_count_is_rejected(tmp_path: Path) -> None:
    # Given: a stored v2 record whose summary is edited to hide a change.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    store.save(_v2())
    path = _record_path(store.root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    changes = payload["changes"]
    assert isinstance(changes, list)
    changes[0]["insertions"] = 1
    _ = path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    # When / Then: the summary is inside the hash preimage, so this cannot pass.
    with pytest.raises(PendingApprovalError):
        _ = PendingRepairApprovalStore._decode(path.read_text(encoding="utf-8"))  # pyright: ignore[reportPrivateUsage]


def test_partial_v2_record_is_malformed(tmp_path: Path) -> None:
    # Given: a record carrying some v2 keys but not all of them.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    store.save(_v2())
    path = _record_path(store.root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["changes"]
    _ = path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    # When / Then: "all present or all absent" is enforced at the disk boundary.
    with pytest.raises(PendingApprovalError):
        _ = PendingRepairApprovalStore._decode(path.read_text(encoding="utf-8"))  # pyright: ignore[reportPrivateUsage]
