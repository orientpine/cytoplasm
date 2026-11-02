"""The gate reads applied todo evidence and records uses separately from the approved board."""

from __future__ import annotations

import hashlib
import http.client
import json
import sqlite3
import threading
from argparse import Namespace
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from automation.project_board import cli, server
from automation.project_board.board_store import BoardStore
from automation.project_board.models import proposal_from_dict
from automation.project_board.orders import OrderStore
from automation.project_board.proposals import ProposalStore, new_record
from automation.project_board.server import make_server
from automation.project_board.service import Board

PROPOSAL = "pb-0123456789ab"
TODO = f"{PROPOSAL}-1"
TOKEN = "t" * 40
GATE = "g" * 40
KEY = {"X-PB-Gate-Token": GATE}
HOST = "board.example"
STAMP = datetime(2026, 11, 2, 21, 9, tzinfo=UTC)
TITLE = "검토 결과 정리"


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[int, BoardStore, OrderStore, ProposalStore]]:
    board = BoardStore(tmp_path / "board.db")
    orders = OrderStore(tmp_path / "orders.db")
    proposals = ProposalStore(tmp_path / "proposals")
    proposal = proposal_from_dict({
        "project": "예시 프로젝트", "summary": "검토", "todos": [{"title": TITLE}],
        "source": {"kind": "mail", "source_id": "test-mail", "subject": "요청"},
    })
    record = replace(new_record(proposal, created_at=STAMP.isoformat()), request_id=PROPOSAL,
                     channel_id="test-channel", message_id="test-card")
    proposals.create(record, proposal)
    board.apply_proposal(PROPOSAL, proposal, message_id="test-card", now=STAMP)

    server = make_server(
        host="127.0.0.1", port=0, token=TOKEN, store=board, today=lambda: date(2026, 11, 2),
        pending=lambda: (), read_source=lambda *a: None, attachment_path=lambda *a: None,
        on_change=lambda: None, orders=orders, gate_token=GATE, card_of=proposals.card_of,
        tailnet_origins=(f"https://{HOST}",), tailnet_logins=("owner@example.com",),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1]), board, orders, proposals
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _fetch(port: int, method: str, path: str, headers: dict[str, str] | None = None) -> tuple[int, str, str]:
    with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=5)) as connection:
        connection.request(method, path, headers=KEY if headers is None else headers)
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8"), response.getheader("Content-Type", "")


def test_card_approved_todo_exposes_only_gate_evidence(served) -> None:
    port, board, orders, proposals = served
    (proposals.root / "proposals.lock").unlink()
    proposal_before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in proposals.root.iterdir() if p.is_file()}
    before = board.path.read_bytes(), board.path.stat().st_mtime_ns
    status, body, content_type = _fetch(port, "GET", f"/api/todos/{TODO}")
    assert status == 200 and content_type == "application/json; charset=utf-8"
    assert json.loads(body) == {
        "todo_id": TODO, "proposal_id": PROPOSAL, "project": "예시 프로젝트", "status": "열림",
        "approved_at": "2026-11-02T21:09:00Z", "auto_applied": False,
        "card": {"channel_id": "test-channel", "message_id": "test-card"},
        "title_sha256": hashlib.sha256(TITLE.encode("utf-8")).hexdigest(), "uses": 0, "first_used_at": "",
    }
    assert (board.path.read_bytes(), board.path.stat().st_mtime_ns) == before
    assert not orders.path.exists()
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in proposals.root.iterdir() if p.is_file()} == proposal_before


@pytest.mark.parametrize("missing_source", [False, True])
def test_auto_applied_or_missing_source_has_no_card(served, missing_source: bool) -> None:
    port, board, _orders, _proposals = served
    with closing(sqlite3.connect(board.path)) as db, db:
        if missing_source:
            db.execute("DELETE FROM sources WHERE proposal_id = ?", (PROPOSAL,))
        else:
            db.execute("UPDATE sources SET message_id = '' WHERE proposal_id = ?", (PROPOSAL,))
    status, body, _ = _fetch(port, "GET", f"/api/todos/{TODO}")
    assert status == 200
    data = json.loads(body)
    assert data["auto_applied"] is True and data["card"] is None


@pytest.mark.parametrize(("message_id", "channel_id"), [
    ("different-card", "test-channel"), ("test-card", ""), ("", "test-channel"), (None, "test-channel"),
])
def test_unbound_or_mismatched_record_cannot_supply_a_card(served, message_id: str | None, channel_id: str) -> None:
    port, _board, _orders, proposals = served
    record = proposals.get(PROPOSAL)
    assert record is not None
    proposals.create(replace(record, message_id=message_id, channel_id=channel_id), proposals.frozen(record))
    status, body, _ = _fetch(port, "GET", f"/api/todos/{TODO}")
    assert status == 200 and json.loads(body)["card"] is None


def test_missing_proposal_record_has_no_card(served) -> None:
    port, board, _orders, _proposals = served
    proposal = proposal_from_dict({
        "project": "다른 예시", "summary": "검토", "todos": [{"title": TITLE}],
        "source": {"kind": "mail", "source_id": "other-mail", "subject": "요청"},
    })
    board.apply_proposal("pb-abcdef012345", proposal, message_id="other-card", now=STAMP)
    status, body, _ = _fetch(port, "GET", "/api/todos/pb-abcdef012345-1")
    data = json.loads(body)
    assert status == 200 and data["auto_applied"] is False and data["card"] is None


@pytest.mark.parametrize("todo_id", [
    "pb-0123456789ab-0", "pb-0123456789ab-01", "pb-0123456789ab-10000", "pb-0123456789aB-1",
    "pb-0123456789a-1", "pb-0123456789abc-1", "pb-0123456789ab-1%0A", "pb-0123456789ab-%D9%A1",
    "pb-0123456789ab-2", "pb-abcdefabcdef-9999",
])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_malformed_or_unknown_todos_are_not_found(served, todo_id: str, method: str) -> None:
    port, _board, orders, _proposals = served
    status, body, _ = _fetch(port, method, f"/api/todos/{todo_id}" + ("/use" if method == "POST" else ""))
    assert (status, body) == (404, "not found\n")
    assert not orders.path.exists()


@pytest.mark.parametrize("headers", [
    {}, {"X-PB-Gate-Token": TOKEN}, {"Cookie": f"pb_token={TOKEN}"},
    {**KEY, "Tailscale-User-Login": "owner@example.com"}, {**KEY, "Tailscale-User-Login": ""},
    {**KEY, "Host": HOST},
])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_todo_gate_refuses_missing_wrong_or_tailnet_authority(served, headers: dict[str, str], method: str) -> None:
    port, _board, orders, _proposals = served
    status, body, _ = _fetch(port, method, f"/api/todos/{TODO}" + ("/use" if method == "POST" else ""), headers)
    assert (status, body) == (403, "forbidden\n")
    assert not orders.path.exists()


@pytest.mark.parametrize(("method", "suffix"), [("GET", "/use"), ("POST", ""), ("POST", "/other")])
def test_todo_routes_require_the_exact_method_shape(served, method: str, suffix: str) -> None:
    port, _board, orders, _proposals = served
    assert _fetch(port, method, f"/api/todos/{TODO}{suffix}")[0] == 404
    assert not orders.path.exists()


def test_post_use_persists_each_gate_use_and_get_never_adds_one(served, monkeypatch: pytest.MonkeyPatch) -> None:
    port, board, orders, _proposals = served
    stamps = iter(("2026-11-02T21:09:00Z", "2026-11-02T21:09:01Z"))
    monkeypatch.setattr("automation.project_board.orders._stamp", lambda _now: next(stamps))
    board_before = board.path.read_bytes()
    first_status, first_body, _ = _fetch(port, "POST", f"/api/todos/{TODO}/use")
    second_status, second_body, _ = _fetch(port, "POST", f"/api/todos/{TODO}/use")
    first, second = json.loads(first_body), json.loads(second_body)
    assert (first_status, second_status, first["uses"], second["uses"]) == (200, 200, 1, 2)
    assert first["first_used_at"] and first["first_used_at"] == second["first_used_at"]
    before = orders.path.read_bytes(), orders.path.stat().st_mtime_ns
    assert json.loads(_fetch(port, "GET", f"/api/todos/{TODO}")[1]) == second
    assert (orders.path.read_bytes(), orders.path.stat().st_mtime_ns) == before
    assert OrderStore(orders.path).todo_usage(TODO) == (2, first["first_used_at"])
    with closing(sqlite3.connect(orders.path)) as db:
        assert db.execute("SELECT todo_id, actor FROM todo_uses ORDER BY id").fetchall() == [(TODO, "gate")] * 2
    assert board.path.read_bytes() == board_before


def test_get_does_not_migrate_legacy_orders_or_change_order_uses(served) -> None:
    port, _board, orders, _proposals = served
    order = orders.add("예시 지시", receiver="관리 도리", by="owner", via="local", now=STAMP)
    with closing(sqlite3.connect(orders.path)) as db, db:
        db.execute("DROP TABLE todo_uses")
    before = orders.path.read_bytes()
    data = json.loads(_fetch(port, "GET", f"/api/todos/{TODO}")[1])
    assert (data["uses"], data["first_used_at"]) == (0, "")
    assert orders.path.read_bytes() == before
    assert _fetch(port, "POST", f"/api/todos/{TODO}/use")[0] == 200
    assert _fetch(port, "POST", f"/api/orders/{order.number}/use")[0] == 200
    data = json.loads(_fetch(port, "GET", f"/api/orders/{order.number}")[1])
    assert data["uses"] == 1 and data["body_sha256"] == hashlib.sha256("예시 지시".encode()).hexdigest()
    assert OrderStore(orders.path).todo_usage(TODO)[0] == 1


def test_serve_wires_card_lookup_to_its_proposal_store(served, monkeypatch: pytest.MonkeyPatch) -> None:
    _port, _board, _orders, proposals = served
    monkeypatch.setattr(cli.Board, "default", lambda: Board(proposals.root))

    class ServerCaptured(Exception):
        pass

    def capture(*, card_of: Callable[[str], tuple[str, str] | None], **_kwargs) -> None:
        assert card_of(PROPOSAL) == ("test-channel", "test-card")
        assert card_of("pb-abcdefabcdef") is None
        raise ServerCaptured

    monkeypatch.setattr(server, "make_server", capture)
    with pytest.raises(ServerCaptured):
        cli.cmd_serve(Namespace(host="127.0.0.1", port=0))
