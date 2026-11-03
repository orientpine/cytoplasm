"""A card-approved dashboard todo as the gate's owner basis (owner 2026-11-03 06:09 KST Q2).

YooDori's todo lane could not take its own source mail's attachments: every owner-command job ended with the
owner's Discord message ids. These cases run the gate's main() against the real dashboard server (loopback,
gate key) and a fake Discord, and check that only that todo's source mail can be read, and only while the
todo is open, card-approved by the owner within 24 hours, with each use recorded on the dashboard.
"""
from __future__ import annotations

import shlex
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from automation.project_board.board_store import BoardStore
from automation.project_board.models import proposal_from_dict
from automation.project_board.orders import OrderStore
from automation.project_board.proposals import ProposalStore, new_record
from automation.project_board.server import make_server
from automation.yoodori_gate import yoodori_read_gate as gate

jobs = gate.yoodori_jobs
PROPOSAL = "pb-0123456789ab"
TODO = f"{PROPOSAL}-1"
GATE_KEY = "g" * 40
UID = "9695144"
OTHER_UID = "555"
NAME = "별첨2.hwp"
PAYLOAD = b"HWP Document File sample"
OWNER = "111111111111111111"
BOT = "222222222222222222"
CHANNEL = "333333333333333333"
CARD = "444444444444444444"
GUILD = "555555555555555555"
ORIGIN = ["--origin-channel-id", "123456789", "--origin-message-id", "987654321"]


@dataclass
class Node:
    board: BoardStore
    orders: OrderStore
    proposals: ProposalStore
    discord: dict[str, object]
    ran: list[gate.Job]

    def uses(self) -> int:
        return self.orders.todo_usage(TODO)[0]

    def todo(self, sql: str) -> None:
        with closing(sqlite3.connect(self.board.path)) as db, db:
            db.execute(sql)


def _mailbox(home: Path) -> None:
    data = home / ".hermes/mailon-runtime/state/data"
    data.mkdir(parents=True)
    with closing(sqlite3.connect(data / "state.db")) as db, db:
        db.execute("CREATE TABLE attachments (uid TEXT, filename TEXT, href TEXT, status TEXT, size_bytes INTEGER,"
                   " error_msg TEXT, attempts INTEGER, local_path TEXT, first_seen INTEGER, last_attempt INTEGER)")
        for uid, name in ((UID, NAME), (OTHER_UID, "남의.pdf")):
            db.execute("INSERT INTO attachments (uid, filename, status) VALUES (?, ?, 'ok')", (uid, name))
            folder = data / "attachments" / uid
            folder.mkdir(parents=True)
            (folder / name).write_bytes(PAYLOAD if uid == UID else b"other mail")


def _reactions(*users: dict) -> list[dict]:
    return [dict(user) for user in users]


@pytest.fixture
def node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Iterator[Node]:
    approved = datetime.now(UTC) - timedelta(hours=getattr(request, "param", 1))
    board = BoardStore(tmp_path / "board/board.db")
    orders = OrderStore(tmp_path / "board/orders.db")
    proposals = ProposalStore(tmp_path / "board/proposals")
    proposal = proposal_from_dict({
        "project": "예시 프로젝트", "summary": "최종보고", "todos": [{"title": "최종보고서 양식 넘기기"}],
        "source": {"kind": "mail", "source_id": UID, "subject": "최종보고 안내"},
    })
    record = replace(new_record(proposal, created_at=approved.isoformat()), request_id=PROPOSAL,
                     channel_id=CHANNEL, message_id=CARD)
    proposals.create(record, proposal)
    board.apply_proposal(PROPOSAL, proposal, message_id=CARD, now=approved)
    server = make_server(
        host="127.0.0.1", port=0, token="t" * 40, store=board, today=lambda: date.today(),
        pending=lambda: (), read_source=lambda *a: None, attachment_path=lambda *a: None,
        on_change=lambda: None, orders=orders, gate_token=GATE_KEY, card_of=proposals.card_of,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = tmp_path / "home"
    _mailbox(home)
    card = f"/channels/{CHANNEL}/messages/{CARD}"
    discord: dict[str, object] = {
        "/users/@me": {"id": BOT, "bot": True},
        card: {"id": CARD, "author": {"id": BOT, "bot": True}},
        f"/channels/{CHANNEL}": {"id": CHANNEL, "guild_id": GUILD, "type": 0},
        f"{card}/reactions/{quote('✅')}?limit=100": _reactions({"id": BOT, "bot": True}, {"id": OWNER}),
        f"{card}/reactions/{quote('⛔')}?limit=100": _reactions({"id": BOT, "bot": True}),
    }

    def fake_discord(path: str, token: str) -> object:
        assert token == "token"
        if path not in discord:
            raise OSError(f"no fake Discord answer for {path}")
        return discord[path]

    ran: list[gate.Job] = []
    monkeypatch.setattr(gate, "HOME", home)
    monkeypatch.setattr(gate, "STAGING", home / ".local/share/yoodori-staging")
    monkeypatch.setattr(gate, "_release_modules", lambda: (None, SimpleNamespace(resolve_token=lambda environ=None: "token")))
    monkeypatch.setattr(gate, "_owner_id", lambda: OWNER)
    monkeypatch.setattr(gate, "_log", lambda *a: None)
    monkeypatch.setattr(gate, "_switched", lambda kind: kind == "drive-save")
    monkeypatch.setattr(gate, "run_job", lambda job: ran.append(job) or 0)
    monkeypatch.setattr(jobs, "_discord_json", fake_discord)
    monkeypatch.setattr(jobs, "dashboard_address", lambda: (f"http://127.0.0.1:{server.server_address[1]}", GATE_KEY))
    monkeypatch.setattr(jobs, "verify_owner_origin", lambda *a, **k: pytest.fail("the owner-message check ran"))
    try:
        yield Node(board, orders, proposals, discord, ran)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _gate(monkeypatch: pytest.MonkeyPatch, *words: str) -> int:
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", shlex.join(words))
    return gate.main()


def _staged() -> list[str]:
    return sorted(p.name for p in gate.STAGING.iterdir()) if gate.STAGING.exists() else []


def test_the_todo_s_own_source_attachment_is_staged_and_the_use_recorded(
    node: Node, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an open todo the owner approved on its card an hour ago, from mail UID.
    # When: YooDori's todo lane asks for that mail's attachment with the todo as basis.
    rc = _gate(monkeypatch, "mail-attachment", UID, NAME, "--origin-todo", TODO)
    # Then: staged like an owner-message request, and the dashboard counts one use.
    staged = gate.STAGING / f"mail-{UID}-1.hwp"
    assert rc == 0 and staged.read_bytes() == PAYLOAD
    assert capsys.readouterr().out.startswith(f"STAGED mail-{UID}-1.hwp ")
    assert node.uses() == 1


def test_another_mail_is_refused_and_nothing_is_recorded(
    node: Node, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    assert _gate(monkeypatch, "mail-attachment", OTHER_UID, "남의.pdf", "--origin-todo", TODO) == 2
    assert "only the todo's own source mail" in capsys.readouterr().err
    assert node.uses() == 0 and _staged() == []


def _weaken(node: Node, how: str) -> None:
    card = f"/channels/{CHANNEL}/messages/{CARD}"
    if how == "auto-applied":
        node.todo(f"UPDATE sources SET message_id = '' WHERE proposal_id = '{PROPOSAL}'")
    elif how == "card-record-mismatch":
        record = node.proposals.get(PROPOSAL)
        assert record is not None
        node.proposals.create(replace(record, message_id="999"), node.proposals.frozen(record))
    elif how in {"완료", "보류"}:
        node.todo(f"UPDATE todos SET status = '{how}' WHERE todo_id = '{TODO}'")
    elif how == "owner-cancelled":
        node.discord[f"{card}/reactions/{quote('⛔')}?limit=100"] = _reactions({"id": OWNER})
    elif how == "no-owner-approval":
        node.discord[f"{card}/reactions/{quote('✅')}?limit=100"] = _reactions({"id": BOT, "bot": True})
    elif how == "someone-else-approved":
        node.discord[f"{card}/reactions/{quote('✅')}?limit=100"] = _reactions({"id": "666666666666666666"})
    elif how == "owner-id-as-bot":
        node.discord[f"{card}/reactions/{quote('✅')}?limit=100"] = _reactions({"id": OWNER, "bot": True})
    elif how == "card-by-another-bot":
        node.discord[card] = {"id": CARD, "author": {"id": "777777777777777777", "bot": True}}
    elif how == "card-unreadable":
        del node.discord[card]
    elif how == "dashboard-down":
        node.discord["__address__"] = ("http://127.0.0.1:9", GATE_KEY)  # nothing listens there
    elif how == "use-fails":
        node.discord["__use_fails__"] = True
    else:
        raise AssertionError(how)


@pytest.mark.parametrize("how", [
    "auto-applied", "card-record-mismatch", "완료", "보류", "owner-cancelled", "no-owner-approval",
    "someone-else-approved", "owner-id-as-bot", "card-by-another-bot", "card-unreadable", "dashboard-down",
    "use-fails",
])
def test_a_weaker_todo_basis_is_refused_before_anything_is_read(
    node: Node, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], how: str,
) -> None:
    # Given: one thing short of a card-approved open todo.
    _weaken(node, how)
    if "__address__" in node.discord:
        monkeypatch.setattr(jobs, "dashboard_address", lambda: node.discord["__address__"])
    if "__use_fails__" in node.discord:
        def broken(todo_id: str, *, now: object = None) -> tuple[int, str]:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(node.orders, "record_todo_use", broken)
    # When / Then: refused, nothing staged, no use counted.
    assert _gate(monkeypatch, "mail-attachment", UID, NAME, "--origin-todo", TODO) == 2
    assert "YOODORI-GATE-REFUSED" in capsys.readouterr().err
    assert _staged() == [] and node.uses() == 0


@pytest.mark.parametrize("node", [25], indirect=True)
def test_an_approval_older_than_24_hours_is_refused(
    node: Node, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    assert _gate(monkeypatch, "mail-attachment", UID, NAME, "--origin-todo", TODO) == 2
    assert "older than 24 hours" in capsys.readouterr().err
    assert _staged() == [] and node.uses() == 0


@pytest.mark.parametrize("words", [
    ["mail-attachment", UID, NAME, "--origin-todo", "pb-xyz-1"],
    ["mail-compose", "--to", "a@example.invalid", "--subject", "s", "--body", "b", "--origin-todo", TODO],
    ["doc", "report", "evidence", "--origin-todo", TODO],
    ["mail-sync", "--origin-todo", TODO],
    ["calendar-request", "--text", "내일 10시 회의", "--origin-todo", TODO],
    ["mail-attachment", UID, NAME, *ORIGIN, "--origin-todo", TODO],
])
def test_other_commands_or_malformed_todo_ids_never_reach_the_dashboard(
    node: Node, monkeypatch: pytest.MonkeyPatch, words: list[str],
) -> None:
    monkeypatch.setattr(jobs, "dashboard_address", lambda: pytest.fail("the dashboard was asked"))
    assert _gate(monkeypatch, *words) == 2
    assert _staged() == [] and node.uses() == 0


def test_drive_save_takes_the_staged_source_attachment_and_runs_without_a_card(
    node: Node, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the source attachment staged under the todo, and someone re-uploading other bytes under its name.
    assert _gate(monkeypatch, "mail-attachment", UID, NAME, "--origin-todo", TODO) == 0
    staged = gate.STAGING / f"mail-{UID}-1.hwp"
    staged.write_bytes(b"not the mail's file")
    # When: the todo lane saves it to Drive.
    rc = _gate(monkeypatch, "drive-save", "--file", staged.name, "--summary", "최종보고서 양식", "--origin-todo", TODO)
    # Then: the cardless save-now job runs on the mailbox's own bytes, bound to the todo and its card.
    assert rc == 0 and staged.read_bytes() == PAYLOAD
    (job,) = node.ran
    assert job.argv[2:4] == ["automation.effect_request", "save-now"]
    assert job.argv[job.argv.index("--file") + 1] == str(staged)
    assert (job.channel_id, job.message_id, job.todo_id) == (CHANNEL, CARD, TODO)
    assert job.todo_link.endswith(f"/{GUILD}/{CHANNEL}/{CARD}")
    assert node.uses() == 2


@pytest.mark.parametrize(("upload", "untouched"), [
    ("report.pdf", True),                  # an ordinary upload
    (f"mail-{OTHER_UID}-1.hwp", True),     # another mail's attachment
    (f"mail-{UID}-7.hwp", False),          # the source mail's uid, but not the place of that attachment
])
def test_drive_save_of_any_other_staged_file_is_refused(
    node: Node, monkeypatch: pytest.MonkeyPatch, upload: str, untouched: bool,
) -> None:
    # Given: a staged file that claims the source attachment's own name beside it.
    gate.STAGING.mkdir(parents=True)
    (gate.STAGING / upload).write_bytes(b"x")
    (gate.STAGING / (upload + ".name")).write_text(NAME, encoding="utf-8")
    before = _staged()
    # When / Then: refused before the use is recorded, and no job runs; a file that is not the source mail's
    # by name is never copied from the mailbox at all.
    assert _gate(monkeypatch, "drive-save", "--file", upload, "--summary", "s", "--origin-todo", TODO) == 2
    assert node.ran == [] and node.uses() == 0
    assert (_staged() == before) is untouched


def test_drive_save_with_a_todo_needs_the_owner_command_switch(
    node: Node, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    assert _gate(monkeypatch, "mail-attachment", UID, NAME, "--origin-todo", TODO) == 0
    monkeypatch.setattr(gate, "_switched", lambda kind: False)
    rc = _gate(monkeypatch, "drive-save", "--file", f"mail-{UID}-1.hwp", "--summary", "s", "--origin-todo", TODO)
    assert rc == 2 and "switch" in capsys.readouterr().err
    assert node.ran == [] and node.uses() == 1


def test_attachment_text_reads_the_source_mail_under_the_todo(node: Node, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _gate(monkeypatch, "mail-attachment-text", UID, NAME, "--origin-todo", TODO) == 0
    (job,) = node.ran
    assert job.argv[3:5] == [UID, NAME] and job.todo_id == TODO and not job.write
    assert node.uses() == 1
    assert _gate(monkeypatch, "mail-attachment-text", OTHER_UID, "남의.pdf", "--origin-todo", TODO) == 2
    assert len(node.ran) == 1 and node.uses() == 1
