"""Owner-approved Obsidian note requests: exact path, bound card, ✅ on the card only, link check.

Regression for 2026-10-07: a proxied "save this note at <path>" request had no producer,
so the agent asked the owner to ✅ its own chat message (nothing consumed it) and
quoted the path without its PARA prefix. These tests pin the replacement contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest

from automation.interop.approval_lifecycle import Outcome
from automation.interop.approval_surface import (
    POLICY_VERSION,
    ApprovalBinding,
    ApprovalKind,
    required_surface,
)
from automation.obsidian_write import gate_binding, links, note_request_gate
from automation.obsidian_write.config import ObsidianWriteConfig, ObsidianWriteError
from automation.obsidian_write.note_request import (
    NoteRequest,
    NoteRequestStore,
    body_sha256,
    build_plan,
    request_key,
    validate_relpath,
)
from automation.obsidian_write.note_request_watch import run_tick
from automation.obsidian_write.writer import WriteReceipt

OWNER = "100"
PATH = "000_PARA/Resource/012_Demo/2026-10-07_Isaac Sim용 현황 (main · sub).md"


class FakeTransport:
    def __init__(self) -> None:
        self.owner_id = OWNER
        self.messages: dict[str, tuple[str, str]] = {}
        self.reactions: dict[tuple[str, str], list[tuple[str, bool]]] = {}

    def api(self, method: str, path: str, payload: object = None) -> object:
        return {"id": "9"}

    def post_message(self, channel_id: str, content: str) -> str:
        message_id = str(500 + len(self.messages))
        self.messages[message_id] = (channel_id, content)
        return message_id

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        self.reactions.setdefault((message_id, emoji), []).append(("777", True))

    def get_message(self, channel_id: str, message_id: str) -> str | None:
        found = self.messages.get(message_id)
        return None if found is None else found[1]

    def get_reaction_users(self, channel_id: str, message_id: str, emoji: str) -> tuple[tuple[str, bool], ...]:
        return tuple(self.reactions.get((message_id, emoji), ()))

    def delete_message(self, channel_id: str, message_id: str) -> None:
        self.messages.pop(message_id, None)


def _mirror(tmp_path: Path) -> Path:
    mirror = tmp_path / "mirror"
    for relative in (
        "000_PARA/Resource/012_Demo/DGX Spark 2대 연결하는 법.md",
        "000_PARA/Resource/012_Demo/DGX Spark 클러스터 구성 가이드.md",
        "000_PARA/Area/assets/diagram.png",
    ):
        target = mirror / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
    return mirror


def _record(store: NoteRequestStore, body: str, *, unresolved: tuple[str, ...] = ()) -> NoteRequest:
    plan = build_plan(validate_relpath(PATH), body, None)
    decision = gate_binding.evaluate(plan)
    record = NoteRequest(
        request_id=decision.action_hash.removeprefix("sha256:")[:16], key=request_key(PATH),
        relpath=PATH, title=plan.title, body_sha256=body_sha256(plan), action_hash=decision.action_hash,
        target_id=decision.target_id, created_at="2026-10-07T15:00:00Z",
        unresolved_links=unresolved, links_checked=True,
    )
    store.create(record, plan)
    return record


def _post(monkeypatch: pytest.MonkeyPatch, store: NoteRequestStore, record: NoteRequest) -> tuple[FakeTransport, NoteRequest]:
    binding = ApprovalBinding(
        ApprovalKind.OBSIDIAN_WRITE, required_surface(ApprovalKind.OBSIDIAN_WRITE), "4242", POLICY_VERSION, "77"
    )
    monkeypatch.setattr(note_request_gate, "reuse_request_thread", lambda *args: None)
    monkeypatch.setattr(note_request_gate, "resolve_new_binding", lambda *args, **kwargs: binding)
    transport = FakeTransport()
    runtime = note_request_gate.DiscordRuntime(transport, object(), OWNER)  # type: ignore[arg-type]
    verdict = note_request_gate.request_approval(record, store, runtime)
    assert verdict.outcome is Outcome.POSTED
    return transport, store.get(record.request_id) or record


def _tick(store: NoteRequestStore, transport: FakeTransport, writes: list[str]) -> None:
    def writer(plan, config, *, approval_context):  # noqa: ANN001, ANN202
        assert gate_binding.evaluate(plan, context=approval_context).allowed
        writes.append(plan.relpath.as_posix())
        return WriteReceipt(plan.relpath, "f" * 64, "origin/main")

    runtime = note_request_gate.DiscordRuntime(transport, object(), OWNER)  # type: ignore[arg-type]
    config = ObsidianWriteConfig("git@example.invalid:vault.git", Path("/nonexistent"), Path("/nonexistent"))
    run_tick(store, runtime, config, token="", now=datetime(2026, 10, 7, tzinfo=UTC), writer=writer)


def test_relpath_is_kept_whole_and_rejects_unsafe_paths() -> None:
    assert validate_relpath(f"  {PATH} ").as_posix() == PATH
    for bad in ("012_Demo/x.md", "000_PARA/../x.md", "/000_PARA/x.md", "000_PARA/x.txt", "000_PARA/a:b.md"):
        with pytest.raises(ObsidianWriteError):
            validate_relpath(bad)


def test_leading_h1_becomes_the_title_once() -> None:
    plan = build_plan(PurePosixPath(PATH), "# 현황 노트\n\n본문", None)
    assert (plan.title, plan.body) == ("현황 노트", "본문")
    assert build_plan(PurePosixPath(PATH), "본문만", None).title == PurePosixPath(PATH).stem


def test_wikilink_forms_reduce_to_their_note_target() -> None:
    body = (
        "[[A|별칭]] [[B#제목]] [[C#^blk]] ![[diagram.png]] | [[D\\|표]] | [[#내부]]\n"
        "`[[인라인코드]]`\n```\n[[펜스]]\n```\n[[A]]"
    )
    assert links.wikilink_targets(body) == ("A", "B", "C", "diagram.png", "D")


def test_unresolved_links_are_checked_against_the_mirror(tmp_path: Path) -> None:
    index = links.build_index(_mirror(tmp_path))
    assert index is not None
    body = (
        "[[DGX Spark 2대 연결하는 법|연결]] [[012_Demo/DGX Spark 클러스터 구성 가이드]] "
        "![[diagram.png]] [[없는 개념]] [[dgx spark 2대 연결하는 법#절]]"
    )
    assert links.unresolved_links(body, index, own_stem="x") == ("없는 개념",)
    assert links.build_index(tmp_path / "missing") is None


def test_related_notes_come_from_the_index_and_must_exist(tmp_path: Path) -> None:
    index = links.build_index(_mirror(tmp_path))
    assert index is not None
    refs = ["000_PARA/Resource/012_Demo/DGX Spark 클러스터 구성 가이드.md", "gone/없는 노트.md"]
    found, source = links.related_notes("DGX Spark 현황", "[[DGX Spark 2대 연결하는 법]]", index,
                                        own_stem="x", search=lambda text: refs)
    assert (found, source) == (("DGX Spark 클러스터 구성 가이드",), "search-index")

    def broken(text: str) -> list[str]:
        raise OSError("rag down")

    fallback, source = links.related_notes("DGX Spark 2대 현황", "", index, own_stem="x", search=broken)
    assert "DGX Spark 2대 연결하는 법" in fallback and source.startswith("filename")


def test_card_binds_full_path_hash_and_lists_unresolved_links(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = NoteRequestStore(tmp_path / "state")
    record = _record(store, "# 현황\n본문 [[없는 개념]]", unresolved=("없는 개념",))
    transport, posted = _post(monkeypatch, store, record)
    content = transport.messages[posted.message_id or ""][1]
    assert f"전체 경로: {PATH}" in content and record.action_hash in content
    assert "[[없는 개념]]" in content and record.body_sha256 in content
    assert (posted.status, posted.approval_thread_id, posted.kind) == ("posted", "4242", "obsidian-write")


def test_owner_check_on_the_card_writes_exactly_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = NoteRequestStore(tmp_path / "state")
    transport, posted = _post(monkeypatch, store, _record(store, "본문"))
    writes: list[str] = []
    _tick(store, transport, writes)
    assert writes == [] and (store.get(posted.request_id) or posted).status == "posted"

    transport.reactions[(posted.message_id or "", "\u2705")].append((OWNER, False))
    _tick(store, transport, writes)
    _tick(store, transport, writes)
    final = store.get(posted.request_id) or posted
    assert writes == [PATH] and (final.status, final.content_sha256) == ("written", "f" * 64)


def test_cancel_wins_and_a_changed_body_is_never_pushed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = NoteRequestStore(tmp_path / "state")
    transport, posted = _post(monkeypatch, store, _record(store, "본문"))
    for emoji in ("\u2705", "\u26d4"):
        transport.reactions[(posted.message_id or "", emoji)].append((OWNER, False))
    writes: list[str] = []
    _tick(store, transport, writes)
    assert writes == [] and (store.get(posted.request_id) or posted).status == "cancelled"

    other = NoteRequestStore(tmp_path / "other")
    transport, posted = _post(monkeypatch, other, _record(other, "본문"))
    other.body_path(posted.request_id).write_text("바뀐 본문", encoding="utf-8")
    transport.reactions[(posted.message_id or "", "\u2705")].append((OWNER, False))
    _tick(other, transport, writes)
    final = other.get(posted.request_id) or posted
    assert writes == [] and final.status == "failed" and "changed" in final.last_error
