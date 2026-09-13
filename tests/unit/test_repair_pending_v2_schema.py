"""The persisted record must reproduce the approval message on its own."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
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
        channel_id="222",
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
        channel_id="222",
        policy_version=POLICY_VERSION,
        content_binding_version=2,
        patch_sha256=DIGEST,
        changes=CHANGES,
        patch_source_path=SOURCE,
    )


def _record_path(root: Path) -> Path:
    return root / f"{hashlib.sha256(TICKET.encode()).hexdigest()}.json"


# Captured on 2886f0a5c: these bytes are machine-consumed by the live exact-text probe.
V1_POSTED = (
    "[repair] 승인 요청\n"
    "- ticket: `t_repair01`\n"
    "- sha256: `0945ce0d76d45dd5a6b8e0de612c8f093d2eb7385ff4bfb471998001cf6f2b3f`\n"
    "- repair_nonce: `nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn`\n"
    "- sandbox: PASS (offline-subset bank + repro GREEN)\n"
    "- cha가 이 메시지에 ✅ 승인 또는 ⛔ 취소 리액션"
)
V2_POSTED = (
    "[repair] 승인 요청\n"
    "- ticket: `t_repair01`\n"
    "- action_hash: `sha256:56779ce0cd3bca72865dda8c74484922b3c7f625ca1ec2e0bc9b5909d7f34081`\n"
    "- patch_sha256: `e083147ea804136e3187aa29377f44f83015175b18593c387b267b95123c0ff5`\n"
    "- changed_files: 2 total, +6/-3\n"
    "  - (신규) docs/새 폴더/기능 소개.md (+4/-0)\n"
    "  - automation/old mod.py → automation/new mod.py (+2/-3)\n"
    "- repair_nonce: `nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn`\n"
    "- sandbox: PASS (offline-subset bank + repro GREEN)\n"
    "- patch_body: 비노출 — ops 호스트의 `/srv/autophagy-private/repair-plans/t_repair01/patch.diff` 에서 확인\n"
    "- cha가 이 메시지에 ✅ 승인 또는 ⛔ 취소 리액션"
)


V3_POSTED = (
    "대상: 수리 승인 (t_repair01)\n"
    "사실: action_hash: sha256:56779ce0cd3bca72865dda8c74484922b3c7f625ca1ec2e0bc9b5909d7f34081; "
    "patch_sha256: e083147ea804136e3187aa29377f44f83015175b18593c387b267b95123c0ff5; "
    "2 files +6/-3; - (신규) docs/새 폴더/기능 소개.md (+4/-0); "
    "- automation/old mod.py → automation/new mod.py (+2/-3); "
    "nonce: nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn; sandbox: PASS; "
    "패치 본문 비노출: `/srv/autophagy-private/repair-plans/t_repair01/patch.diff` "
    "(승인 요청; 만료: 2026-07-30T09:00:00+00:00)\n"
    "위치: 이 메시지\n"
    "인계: 소유자: 위 위치 · 반응 ✅ 승인 또는 ⛔ 취소; 다음: 승인된 패치만 반영\n"
    "되돌리기: 해당 없음; 취소 시: 패치 미반영, 티켓 재개"
)


def test_stored_v3_replays_fixed_wire_bytes_when_loaded(tmp_path: Path) -> None:
    # Given: an independently fixed v3 record, persisted before replay.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    store.save(replace(_v2(), render_version=3))
    stored = store.get(TICKET)
    assert stored is not None
    # When: the binding gate reconstructs the wire bytes from the stored record.
    actual = approval_request_content(stored).encode("utf-8")
    # Then: shared-renderer drift must not silently invalidate a published card.
    assert actual == V3_POSTED.encode("utf-8")


@pytest.mark.parametrize("record,posted", [(_legacy(), V1_POSTED), (_v2(), V2_POSTED)], ids=["v1", "v2"])
def test_stored_record_replays_base_bytes_when_render_version_is_absent(
    tmp_path: Path, record: PendingRepairApproval, posted: str,
) -> None:
    # Given: a stored record from before envelope cards existed.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    store.save(record)
    stored = store.get(TICKET)
    assert stored is not None
    # When: the exact-text probe reconstructs its expected message from disk alone.
    actual = approval_request_content(stored).encode("utf-8")
    # Then: the bytes equal the independently captured base output.
    assert actual == posted.encode("utf-8")


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
