"""Stored repair approvals survive renderer failure, including pre-digest cards."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from automation.interop import owner_message
from automation.interop.approval_types import Probe
from automation.repair import repair_approval_render, repair_ops_approval_gate
from automation.repair.repair_approval_content import approval_content_matches
from automation.repair.repair_ops_pending import PendingRepairApprovalStore, PostingOwnerApproval
from automation.repair.repair_ops_reaction_watch import ReactionDecision, RepairApprovalWatcher, reaction_decision
from automation.stored_content import hash_parts
from tests.unit.test_repair_approval_envelope import NOW, PATCH, TICKET
from tests.unit.test_repair_approval_watch import FakeRepairCommands
from tests.unit.test_repair_pending_v2_schema import _legacy, _v2
from tests.unit.test_repair_single_live_request import FakeApprovalSurface


@pytest.mark.parametrize("version", [1, 2, 3])
@pytest.mark.parametrize("digest", [False, True], ids=["pre-digest", "digest"])
@pytest.mark.parametrize("clicked", [False, True], ids=["pending", "approved"])
def test_stored_cards_never_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int, digest: bool, clicked: bool,
) -> None:
    source = tmp_path / "patch.diff"
    _ = source.write_text(PATCH, encoding="utf-8")
    store = PendingRepairApprovalStore(tmp_path / "pending")
    surface = FakeApprovalSurface()
    producer = PostingOwnerApproval("111", store, surface, lambda: NOW, nonce=lambda: "n" * 32)
    assert producer.permits(TICKET, source) is False
    record = store.get(TICKET)
    assert record is not None
    if version == 1:
        record = replace(_legacy(), ticket_id=TICKET, created_at=NOW)
        from automation.repair.repair_ops_approval import repair_action_hash
        record = replace(record, action_hash=repair_action_hash(TICKET, record.patch_name))
    else:
        record = replace(record, render_version=2 if version == 2 else 3)
    content = repair_approval_render.approval_request_content(record)
    record = replace(record, message_id="msg-1", content_sha256=hash_parts(content) if digest else None)
    store.save(record)
    surface.messages[record.message_id] = content
    if clicked:
        surface.reactions[(record.message_id, "✅")] = (("111", False),)
    before = next(store.root.glob("*.json")).read_bytes()
    messages = dict(surface.messages)
    calls: list[str] = []

    def fail(*_args: object, **_kwargs: object) -> str:
        calls.append("render")
        print("RENDER CALLED -> RAISE")
        raise owner_message.OwnerMessageError(detail="test.capability")

    monkeypatch.setattr(owner_message, "render", fail)
    monkeypatch.setattr(repair_ops_approval_gate, "approval_request_content", fail)
    monkeypatch.setattr(repair_approval_render, "approval_request_content", fail)
    for name in ("_render_v1", "_render_v2", "_render_v3"):
        monkeypatch.setattr(repair_approval_render, name, fail)
    # v1 uses the older action binding, so its pending producer would supersede it.
    if version != 1 or clicked:
        assert producer.permits(TICKET, source) is False
        assert next(store.root.glob("*.json")).read_bytes() == before
    expected = Probe.APPROVED if clicked else Probe.BOUND_PENDING
    assert repair_ops_approval_gate.probe_pending(record, "111", surface) is expected
    assert reaction_decision(record, "111", surface) is (
        ReactionDecision.APPROVED if clicked else ReactionDecision.PENDING
    )
    commands = FakeRepairCommands()
    audit = tmp_path / "audit.jsonl"
    watcher = RepairApprovalWatcher(store, surface, commands, "111", audit, lambda: NOW)
    watcher.run_once()
    if not clicked:
        assert commands.applied == [] and store.get(TICKET) == record
        surface.reactions[(record.message_id, "✅")] = (("111", False),)
        watcher.run_once()
    assert commands.applied == [record] and commands.discarded == []
    assert store.get(TICKET) is None
    assert json.loads(audit.read_text(encoding="utf-8"))["result"]["status"] == "approved"
    assert calls == [] and surface.messages == messages and surface.calls == ["post:msg-1"]
    print(f"v{version} digest={digest} clicked={clicked}: approved consumed; render_calls={calls}; no delete/repost")


@pytest.mark.parametrize("version", [1, 2, 3])
@pytest.mark.parametrize("digest", [False, True])
@pytest.mark.parametrize("damage", ["body", "nonce", "hash", "newline", "prefix"])
def test_stored_card_bytes_remain_bound(version: int, digest: bool, damage: str) -> None:
    record = _legacy() if version == 1 else replace(_v2(), render_version=2 if version == 2 else 3)
    content = repair_approval_render.approval_request_content(record)
    record = replace(record, content_sha256=hash_parts(content) if digest else None)
    assert approval_content_matches(record, content)
    damaged = {
        "body": content + "X", "nonce": content.replace(record.nonce, "other"),
        "hash": content.replace(record.action_hash, "sha256:" + "f" * 64),
        "newline": content.replace("\n", "\r\n"), "prefix": "X" + content,
    }[damage]
    assert not approval_content_matches(record, damaged)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_digest_cannot_transfer_consent_to_changed_record(version: int) -> None:
    record = _legacy() if version == 1 else replace(_v2(), render_version=2 if version == 2 else 3)
    content = repair_approval_render.approval_request_content(record)
    record = replace(record, content_sha256=hash_parts(content))
    assert not approval_content_matches(replace(record, nonce="other"), content)
    assert not approval_content_matches(replace(record, action_hash="sha256:" + "f" * 64), content)
    assert not approval_content_matches(replace(record, content_sha256="f" * 64), content)


@pytest.mark.parametrize("clicked", [False, True], ids=["pending", "approved"])
def test_real_cli_transport_and_watcher_reuse_without_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clicked: bool,
) -> None:
    from urllib.parse import unquote
    from urllib.request import Request

    from automation.repair import repair_ops_cli, repair_ops_discord, repair_ops_reaction_watch
    from tests.unit.test_repair_approval_binding import (
        _FakeDiscordHttp, _FakeHttpResponse, _ThreadOpeningOpsDirectory,
    )
    from tests.unit.test_repair_approval_preflight import _Clock

    monkeypatch.setattr(repair_ops_cli, "datetime", _Clock)
    monkeypatch.setattr(repair_ops_reaction_watch, "datetime", _Clock)
    source = tmp_path / "patch.diff"
    _ = source.write_text(PATCH, encoding="utf-8")
    directory = _ThreadOpeningOpsDirectory()
    http = _FakeDiscordHttp()
    reactions: list[bool] = [clicked]
    requests: list[str] = []

    def endpoint(request: Request) -> _FakeHttpResponse:
        requests.append(request.get_method())
        if request.method == "GET" and "/reactions/" in request.full_url:
            approved = reactions[0] and "/✅?" in unquote(request.full_url)
            return _FakeHttpResponse(b'[{"id":"111","bot":false}]' if approved else b"[]")
        return http(request)

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "synthetic")
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", "111")
    monkeypatch.setenv("REPAIR_APPROVAL_PENDING_ROOT", str(tmp_path / "pending"))
    monkeypatch.setenv("REPAIR_APPROVAL_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.delenv("REPAIR_E2E_SECRET", raising=False)
    monkeypatch.setattr(repair_ops_discord, "directory_for_ops", lambda token, owner: directory)
    monkeypatch.setattr(repair_ops_discord, "_open_discord", endpoint)
    config = repair_ops_cli.RepairOpsConfig(
        TICKET, tmp_path / "checkout", tmp_path / "logs", tmp_path / "plans",
        tmp_path / "audit.jsonl", None, None,
    )
    producer = repair_ops_cli._approval(config)
    assert isinstance(producer, PostingOwnerApproval)
    assert producer.permits(TICKET, source) is False
    record = producer.store.get(TICKET)
    assert record is not None
    messages = dict(http.messages)
    calls: list[str] = []

    def fail(*_args: object, **_kwargs: object) -> str:
        calls.append("render")
        raise owner_message.OwnerMessageError(detail="test.capability")

    monkeypatch.setattr(owner_message, "render", fail)
    assert producer.permits(TICKET, source) is False
    api = repair_ops_discord.configured_setup().resolve(TICKET, (repair_ops_approval_gate.request_of(record),))
    commands = FakeRepairCommands()
    monkeypatch.setattr(repair_ops_discord, "configured_discord", lambda: api)
    monkeypatch.setattr(repair_ops_reaction_watch, "CliRepairApprovalCommands", lambda repair_cli, token: commands)
    monkeypatch.setattr(repair_ops_reaction_watch, "load_approval_reminder_config", lambda: None)
    assert repair_ops_reaction_watch.main() == 0
    if not clicked:
        assert commands.applied == []
        reactions[0] = True
        assert repair_ops_reaction_watch.main() == 0
    assert commands.applied == [record] and commands.discarded == []
    assert producer.store.get(TICKET) is None
    assert calls == [] and http.messages == messages and len(http.posts) == 1
    assert len(directory.opened) == 1 and "DELETE" not in requests
    print(f"CLI + real REST adapter + watcher.main clicked={clicked}: consumed; render_calls={calls}; one original thread/card")


# Actual cards and JSON records posted by 53478efd7; never regenerate with HEAD.
_BASE_CARDS = {
    'delimiter': (
        '{"ticket_id":"t_legacy","patch_name":"patch.diff","action_hash":"sha256:dec46f9e0776e408dd0656bfecf7686f572222695795490f221e98816d7fb9c4","nonce":"nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn","message_id":"msg-1","created_at":"2026-09-12T09:00:00+00:00","kind":null,"surface":null,"channel_id":null,"policy_version":null,"render_version":3,"content_binding_version":2,"patch_sha256":"70683bab179117b29539f74fcd31f71543a0e8c9783120ccaac7ff4636ebd6e6","changes":[{"deletions":1,"insertions":1,"new_path":"semi; nonce: value.py","old_path":"semi; nonce: value.py"}],"patch_source_path":"/tmp/omux-t43-gate-r1/legacy-repro-data/delimiter/patch.diff"}',
        '대상: 수리 승인 (t_legacy)\n사실: action_hash: sha256:dec46f9e0776e408dd0656bfecf7686f572222695795490f221e98816d7fb9c4; patch_sha256: 70683bab179117b29539f74fcd31f71543a0e8c9783120ccaac7ff4636ebd6e6; 1 files +1/-1; - semi; nonce: value.py (+1/-1); nonce: nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn; sandbox: PASS; 패치 본문 비노출: `/tmp/omux-t43-gate-r1/legacy-repro-data/delimiter/patch.diff` (승인 요청; 만료: 2026-09-13T09:00:00+00:00)\n위치: 이 메시지\n인계: 소유자: 위 위치 · 반응 ✅ 승인 또는 ⛔ 취소; 다음: 승인된 패치만 반영\n되돌리기: 해당 없음; 취소 시: 패치 미반영, 티켓 재개',
    ),
    'whitespace': (
        '{"ticket_id":"t_legacy","patch_name":"patch.diff","action_hash":"sha256:38812afd6929329c1a5bbdf5e27a5e7e3ff4c2de17dcd4f9fd9e07f1a270171a","nonce":"nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn","message_id":"msg-1","created_at":"2026-09-12T09:00:00+00:00","kind":null,"surface":null,"channel_id":null,"policy_version":null,"render_version":3,"content_binding_version":2,"patch_sha256":"92f35afad3faf9dbfa06e6ddb24e04a01d977efb1c6487e64f41107ba7451aae","changes":[{"deletions":1,"insertions":1,"new_path":" ","old_path":" "}],"patch_source_path":"/tmp/omux-t43-gate-r1/legacy-repro-data/whitespace/patch.diff"}',
        '대상: 수리 승인 (t_legacy)\n사실: action_hash: sha256:38812afd6929329c1a5bbdf5e27a5e7e3ff4c2de17dcd4f9fd9e07f1a270171a; patch_sha256: 92f35afad3faf9dbfa06e6ddb24e04a01d977efb1c6487e64f41107ba7451aae; 1 files +1/-1; - (+1/-1); nonce: nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn; sandbox: PASS; 패치 본문 비노출: `/tmp/omux-t43-gate-r1/legacy-repro-data/whitespace/patch.diff` (승인 요청; 만료: 2026-09-13T09:00:00+00:00)\n위치: 이 메시지\n인계: 소유자: 위 위치 · 반응 ✅ 승인 또는 ⛔ 취소; 다음: 승인된 패치만 반영\n되돌리기: 해당 없음; 취소 시: 패치 미반영, 티켓 재개',
    ),
    'trailer': (
        '{"ticket_id":"t_legacy","patch_name":"patch.diff","action_hash":"sha256:b065f0687ee52fc1bf4a061501e84ebfb220114a7c8bff81e6af4c753784763a","nonce":"nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn","message_id":"msg-1","created_at":"2026-09-12T09:00:00+00:00","kind":null,"surface":null,"channel_id":null,"policy_version":null,"render_version":3,"content_binding_version":2,"patch_sha256":"158ac80cbbb5c9e8e4bcdec441853f96aaf00e1a8bc8fe8d9ae3f1d3a06ea972","changes":[{"deletions":1,"insertions":1,"new_path":"semi; nonce: value; sandbox: PASS; tail.py","old_path":"semi; nonce: value; sandbox: PASS; tail.py"}],"patch_source_path":"/tmp/omux-t43-r2/trailer/path; nonce: tail; sandbox: PASS/patch.diff"}',
        '대상: 수리 승인 (t_legacy)\n사실: action_hash: sha256:b065f0687ee52fc1bf4a061501e84ebfb220114a7c8bff81e6af4c753784763a; patch_sha256: 158ac80cbbb5c9e8e4bcdec441853f96aaf00e1a8bc8fe8d9ae3f1d3a06ea972; 1 files +1/-1; - semi; nonce: value; sandbox: PASS; tail.py (+1/-1); nonce: nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn; sandbox: PASS; 패치 본문 비노출: `/tmp/omux-t43-r2/trailer/path; nonce: tail; sandbox: PASS/patch.diff` (승인 요청; 만료: 2026-09-13T09:00:00+00:00)\n위치: 이 메시지\n인계: 소유자: 위 위치 · 반응 ✅ 승인 또는 ⛔ 취소; 다음: 승인된 패치만 반영\n되돌리기: 해당 없음; 취소 시: 패치 미반영, 티켓 재개',
    ),
}


# Gate round-2 artifacts plus round-3 BASE-produced family hunt. These are
# machine-consumed binding bytes, not snapshots of today's display renderer.
_ROUND3_CARDS: dict[str, tuple[str, str]] = json.loads(
    (Path(__file__).parent / "fixtures" / "repair_approval_base_r3.json").read_text(encoding="utf-8")
)
_BASE_CARDS.update(_ROUND3_CARDS)


@pytest.mark.parametrize("case", _BASE_CARDS)
@pytest.mark.parametrize("clicked", [False, True], ids=["pending", "approved"])
def test_base_pathological_card_is_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, clicked: bool,
) -> None:
    stored, content = _BASE_CARDS[case]
    store = PendingRepairApprovalStore(tmp_path / "pending")
    store.root.mkdir()
    # The persisted BASE record is read unchanged, not migrated or re-rendered.
    ticket = json.loads(stored)["ticket_id"]
    record_path = store.root / f"{hashlib.sha256(ticket.encode()).hexdigest()}.json"
    _ = record_path.write_text(stored, encoding="utf-8")
    record = store.get(ticket)
    assert record is not None and record.content_sha256 is None
    surface = FakeApprovalSurface()
    surface.messages[record.message_id] = content
    if clicked:
        surface.reactions[(record.message_id, "✅")] = (("111", False),)
    calls: list[str] = []

    def fail(*_args: object, **_kwargs: object) -> str:
        calls.append("render")
        raise owner_message.OwnerMessageError(detail="test.capability")

    monkeypatch.setattr(owner_message, "render", fail)
    monkeypatch.setattr(repair_ops_approval_gate, "approval_request_content", fail)
    monkeypatch.setattr(repair_approval_render, "approval_request_content", fail)
    for name in ("_render_v1", "_render_v2", "_render_v3"):
        monkeypatch.setattr(repair_approval_render, name, fail)
    assert repair_ops_approval_gate.probe_pending(record, "111", surface) is (
        Probe.APPROVED if clicked else Probe.BOUND_PENDING
    )
    assert reaction_decision(record, "111", surface) is (
        ReactionDecision.APPROVED if clicked else ReactionDecision.PENDING
    )
    commands = FakeRepairCommands()
    audit = tmp_path / "audit.jsonl"
    watcher = RepairApprovalWatcher(store, surface, commands, "111", audit, lambda: record.created_at)
    watcher.run_once()
    if not clicked:
        assert commands.applied == [] and record_path.read_text(encoding="utf-8") == stored
        surface.reactions[(record.message_id, "✅")] = (("111", False),)
        assert reaction_decision(record, "111", surface) is ReactionDecision.APPROVED
        watcher.run_once()
    assert commands.applied == [record] and commands.discarded == []
    assert store.get(record.ticket_id) is None
    assert json.loads(audit.read_text(encoding="utf-8"))["result"]["status"] == "approved"
    assert calls == [] and surface.calls == [] and surface.messages == {record.message_id: content}
    # Exact binding still rejects changed facts, including delimiter-shaped ones.
    assert not approval_content_matches(replace(record, nonce="other"), content)
    assert not approval_content_matches(record, content + "X")
    print(f"BASE {case} clicked={clicked}: approved consumed; zero renders; original card retained")


@pytest.mark.parametrize("case", ["source-space", "source-tab", "ticket-leading", "ticket-trailing"])
def test_base_card_whitespace_trimmed_alteration_is_rejected(tmp_path: Path, case: str) -> None:
    stored, content = _ROUND3_CARDS[case]
    record = PendingRepairApprovalStore._decode(stored)
    if case.startswith("source"):
        altered = content.replace("patch.diff `", "patch.diff`")
    else:
        altered = content.replace("( t_legacy)", "(t_legacy)").replace("(t_legacy )", "(t_legacy)")
    assert altered != content
    surface = FakeApprovalSurface()
    surface.messages[record.message_id] = altered
    surface.reactions[(record.message_id, "✅")] = (("111", False),)
    # Independent rejection assertion: do not let the original's acceptance
    # assertion hide the more dangerous direction when this fix is mutated.
    assert not approval_content_matches(record, altered)
    assert repair_ops_approval_gate.probe_pending(record, "111", surface) is Probe.BINDING_MISMATCH
    assert reaction_decision(record, "111", surface) is ReactionDecision.INVALID
    store = PendingRepairApprovalStore(tmp_path / "pending")
    store.save(record)
    commands = FakeRepairCommands()
    RepairApprovalWatcher(store, surface, commands, "111", tmp_path / "audit.jsonl", lambda: record.created_at).run_once()
    assert commands.applied == [] and commands.discarded == []
    assert store.get(record.ticket_id) == record
    assert surface.calls == [] and surface.messages == {record.message_id: altered}
