"""EXACTLY ONE live owner-approval request per ``budget:{mail_to}``.

A budget draft sends a real 과제비 request mail, so a duplicate approval message
is a duplicate mail waiting for a second ✅. The stored ``message_id`` is written
only by the gate's commit: never replaced, only superseded or left alone, and it
carries the channel binding the message was actually posted to.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import select
import sys
from email.message import Message
from multiprocessing.process import BaseProcess
from multiprocessing.synchronize import Barrier as ProcessBarrier
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "budget" / "scripts"))

import budget_approval  # noqa: E402
import budget_cli  # noqa: E402
import budget_confirm  # noqa: E402
import budget_core  # noqa: E402
import budget_gate  # noqa: E402
import budget_store  # noqa: E402

from automation.interop.approval_surface import POLICY_VERSION  # noqa: E402

OWNER = "owner-budget"
APPROVALS_CHANNEL = "1528936606856122421"
DM_CHANNEL = "1526487935975952385"
MAIL_TO = "office@example.invalid"
# IPC 대기 상한 — 불변식이 아니라 hang 방지용이다. 직렬화 판정은 결과 단언(정확히 1 POST +
# 나머지 defer)이 한다.
#
# 자매 테스트(mail)에서 같은 값을 올렸던 이유는 '느린 러너' 진단이었고 그것은 틀렸다 —
# 진짜 원인은 비원자적 `write_json` 이 만든 찢어진 읽기였다
# (tests/unit/test_gate_write_json_atomic.py 가 고정). 이 상한은 hang 경계로만 남긴다.
TIMEOUT_S = 60
ROWS_A = [["인건비", "100", "10", "90", "2026-07-14"]]
ROWS_B = [["인건비", "100", "20", "80", "2026-07-15"]]


class FakeDiscord:
    """Offline approval surface; ``log_path`` collects POSTs from every forked process."""

    def __init__(self, calls: list[str], log_path: Path | None = None) -> None:
        self.calls, self.log_path = calls, log_path
        self.contents: dict[str, str] = {}
        self.approved: set[str] = set()
        self.posts = 0

    def api(self, method: str, path: str, payload: dict | None = None):
        parts = path.strip("/").split("/")
        if method == "POST" and path == "/users/@me/channels":
            return {"id": DM_CHANNEL}
        if method == "GET" and path == f"/channels/{DM_CHANNEL}":
            return {"id": DM_CHANNEL, "type": 1, "name": "", "recipients": [{"id": OWNER}]}
        if method == "POST" and parts[-1] == "messages":
            self.posts += 1
            message_id = f"m-{self.posts}"
            self.contents[message_id] = str((payload or {}).get("content", ""))
            self.calls.append(f"POST:{message_id}")
            if self.log_path is not None:
                with self.log_path.open("a", encoding="utf-8") as handle:
                    handle.write(message_id + "\n")
            return {"id": message_id}
        if method == "GET" and len(parts) == 2:
            return {"id": parts[1], "type": 0, "name": "approvals"}
        message_id = parts[3] if len(parts) > 3 else ""
        if method == "PUT":
            return None
        if method == "DELETE":
            self.contents.pop(message_id, None)
            self.calls.append(f"DELETE:{message_id}")
            return None
        if method == "GET" and len(parts) > 5:
            emoji = unquote(parts[5].split("?", 1)[0])
            if emoji == budget_confirm.APPROVE_EMOJI and message_id in self.approved:
                return [{"id": OWNER, "bot": False}]
            return []
        if method == "GET":
            content = self.contents.get(message_id)
            if content is None:
                raise HTTPError("https://discord.invalid", 404, "missing", Message(), None)
            return {"id": message_id, "content": content}
        raise AssertionError(f"unexpected Discord call: {method} {path}")




@pytest.fixture
def budget_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[FakeDiscord, list[str], Path]:
    calls: list[str] = []
    fake = FakeDiscord(calls)
    config = tmp_path / "budget-config.json"
    config.write_text(json.dumps({"mail_to": MAIL_TO}), encoding="utf-8")
    interop = tmp_path / "interop-config.json"
    interop.write_text(
        json.dumps({"owner_id": OWNER, "personal_approvals_channel_id": APPROVALS_CHANNEL}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("BUDGET_GATE_DIR", str(tmp_path / "budget-gate"))
    monkeypatch.setenv("BUDGET_DB", str(tmp_path / "budget.db"))
    monkeypatch.setenv("BUDGET_CONFIG", str(config))
    monkeypatch.setenv("INTEROP_CONFIG", str(interop))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setattr(budget_confirm, "owner_id", lambda: OWNER)
    monkeypatch.setattr(budget_confirm, "_api", fake.api)
    return fake, calls, tmp_path / "budget-gate" / "drafts"


def _draft(new_hash: str = "n" * 64, claim_key: str = "k-1") -> dict:
    return budget_gate.create_draft(
        changes=[budget_core.Change("재료비", "집행액", "0", "50")],
        subject="[과제비] 원장 변경 통지", body="본문", recipient=MAIL_TO,
        prev_hash="p" * 64, new_hash=new_hash, claim_key=claim_key,
    )


def _stored(drafts: Path, draft_id: str) -> dict:
    return json.loads((drafts / f"{draft_id}.json").read_text(encoding="utf-8"))


def _live() -> list[str]:
    """Every draft that still holds a Discord approval message id."""
    drafts = budget_gate.list_drafts()
    return [str(record["message_id"]) for record in drafts if record.get("message_id")]


def _log_writes(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    original = budget_gate.write_json

    def write(path: Path, record: dict) -> None:
        calls.append(f"WRITE:{Path(path).stem}")
        original(path, record)

    monkeypatch.setattr(budget_gate, "write_json", write)


def _sheet(path: Path, rows: list[list[str]]) -> Path:
    values = [["[규칙]"], ["1"], ["2"], ["3"], [], list(budget_core.HEADER_EXPECTED), *rows]
    path.write_text(
        json.dumps({"majorDimension": "ROWS", "values": values}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _wait_byte(fd: int) -> bytes:
    readable, _, _ = select.select([fd], [], [], TIMEOUT_S)
    assert readable, "pipe handshake timed out"
    return os.read(fd, 1)


def _read_result(fd: int) -> tuple[str, str]:
    readable, _, _ = select.select([fd], [], [], TIMEOUT_S)
    assert readable, "result pipe timed out"
    outcome, reason = os.read(fd, 128).decode().split("|", maxsplit=1)
    return outcome, reason


def _join(process: BaseProcess) -> None:
    process.join(timeout=TIMEOUT_S)
    if process.is_alive():
        process.kill()
        process.join(timeout=TIMEOUT_S)
        pytest.fail("child process timed out")
    assert process.exitcode == 0


def _producer(draft: dict, barrier: ProcessBarrier, pipes: tuple[int, int, int]) -> None:
    result, ready, release = pipes
    barrier.wait(timeout=TIMEOUT_S)
    original, blocked = budget_approval._pending_drafts, False

    def blocking() -> tuple[tuple[Path, dict], ...]:
        nonlocal blocked
        if not blocked:
            blocked = True
            os.write(ready, b"1")
            _wait_byte(release)
        return original()

    budget_approval._pending_drafts = blocking
    verdict = budget_approval.request_approval(draft)
    reason = verdict.reason.value if verdict.reason is not None else "none"
    os.write(result, f"{verdict.outcome.value}|{reason}".encode())


def test_second_request_for_the_same_draft_and_hash_posts_nothing(
    budget_env: tuple[FakeDiscord, list[str], Path],
) -> None:
    # Given: one pending draft already bound to a live #approvals message
    fake, _calls, drafts = budget_env
    record = _draft()
    assert budget_approval.post_for_approval(record) == "m-1"
    # When: the same draft reaches the producer again with an unchanged hash
    reused = budget_approval.post_for_approval(record)
    # Then: nothing is posted and the stored message id is untouched
    assert reused == "m-1"
    assert fake.posts == 1
    assert _stored(drafts, record["id"])["message_id"] == "m-1"
    assert _live() == ["m-1"]


def test_changed_content_deletes_the_message_before_dropping_the_record(
    budget_env: tuple[FakeDiscord, list[str], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a live request for this recipient and a newer ledger change
    fake, calls, drafts = budget_env
    stale = _draft()
    assert budget_approval.post_for_approval(stale) == "m-1"
    fresh = _draft(new_hash="q" * 64, claim_key="k-2")
    _log_writes(monkeypatch, calls)
    # When: the producer requests approval for the newer change on the same key
    assert budget_approval.post_for_approval(fresh) == "m-2"
    # Then: the stale message dies BEFORE its record is unbound, then one post happens
    assert calls.index("DELETE:m-1") < calls.index(f"WRITE:{stale['id']}")
    assert calls.index(f"WRITE:{stale['id']}") < calls.index("POST:m-2")
    assert fake.posts == 2
    assert _live() == ["m-2"]
    assert _stored(drafts, stale["id"])["message_id"] == ""


def test_owner_approved_request_is_deferred_without_deleting_or_touching_it(
    budget_env: tuple[FakeDiscord, list[str], Path],
) -> None:
    # Given: a live request the owner has already ✅
    fake, calls, drafts = budget_env
    record = _draft()
    assert budget_approval.post_for_approval(record) == "m-1"
    fake.approved.add("m-1")
    before = (drafts / f"{record['id']}.json").read_bytes()
    # When: the producer runs again for the same key
    with pytest.raises(budget_gate.GateError) as refusal:
        budget_approval.post_for_approval(_draft(new_hash="q" * 64, claim_key="k-2"))
    # Then: the owner's decision survives — no delete, no repost, no record change
    assert refusal.value.exit_code == 1
    assert "DELETE:m-1" not in calls
    assert fake.posts == 1
    assert (drafts / f"{record['id']}.json").read_bytes() == before


def test_corrupt_draft_record_refuses_the_request_without_posting(
    budget_env: tuple[FakeDiscord, list[str], Path],
) -> None:
    # Given: an unreadable draft file beside a fresh, unposted draft
    fake, _calls, drafts = budget_env
    record = _draft()
    (drafts / "bad.json").write_text("{not-json\n", encoding="utf-8")
    # When: the producer asks for owner approval
    with pytest.raises(budget_gate.GateError) as refusal:
        budget_approval.post_for_approval(record)
    # Then: it refuses as a store problem and posts nothing
    assert refusal.value.exit_code == 3
    assert fake.posts == 0
    assert _stored(drafts, record["id"])["message_id"] == ""


def test_two_concurrent_producers_post_exactly_once(
    budget_env: tuple[FakeDiscord, list[str], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one unposted draft and two real producer processes racing for its key
    _fake, _calls, _drafts = budget_env
    record = _draft()
    log = tmp_path / "posts.log"
    monkeypatch.setattr(budget_confirm, "_api", FakeDiscord([], log).api)
    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(2)
    (ready_r, ready_w), (release_r, release_w) = os.pipe(), os.pipe()
    result_pipes = [os.pipe(), os.pipe()]
    processes = [
        ctx.Process(target=_producer, args=(record, barrier, (pipe[1], ready_w, release_r)))
        for pipe in result_pipes
    ]
    # When: both tick at the same moment (the cron wrapper has no flock)
    for process in processes:
        process.start()
    _wait_byte(ready_r)
    reads = [pipe[0] for pipe in result_pipes]
    available, _, _ = select.select(reads, [], [], TIMEOUT_S)
    assert len(available) == 1
    results = [_read_result(available[0])]
    os.write(release_w, b"1")
    results += [_read_result(fd) for fd in reads if fd not in available]
    for process in processes:
        _join(process)
    # Then: the lease serializes them — exactly one POST, the other defers
    assert sorted(results) == [("deferred", "lease-held"), ("posted", "none")]
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1
    assert len(_live()) == 1


def test_claim_change_still_short_circuits_a_second_draft_for_the_same_key(
    budget_env: tuple[FakeDiscord, list[str], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a stored baseline and a change whose claim key is already taken
    fake, _calls, _drafts = budget_env
    sheet = _sheet(tmp_path / "sheet.json", ROWS_A)
    monkeypatch.setenv("BUDGET_SHEET_FILE", str(sheet))
    assert budget_cli._snapshot(post=True) == 0
    _sheet(sheet, ROWS_B)
    db = tmp_path / "budget.db"
    baseline = budget_store.latest_snapshot(db)
    assert baseline is not None
    rows = budget_core.data_rows(
        budget_core.parse_balance_payload(sheet.read_text(encoding="utf-8"))
    )
    key = budget_core.claim_key(baseline[0], budget_core.snapshot_hash(rows))
    assert budget_store.claim_change(db, key, "t0") is True
    _ = capsys.readouterr()
    # When: the snapshot pipeline reaches the same change again
    assert budget_cli._snapshot(post=True) == 0
    # Then: the legacy SQLite claim still refuses to draft, and nothing is posted
    assert capsys.readouterr().out == (
        f"ALREADY-CLAIMED key={key} (스냅샷만 전진, 초안 중복 없음)\n"
    )
    assert budget_gate.list_drafts() == []
    assert fake.posts == 0


def test_the_commit_persists_the_surface_the_message_was_posted_to(
    budget_env: tuple[FakeDiscord, list[str], Path],
) -> None:
    # Given: one unposted draft and the gate's only message_id writer
    _fake, _calls, drafts = budget_env
    record = _draft()
    # When: the producer posts it for owner approval
    assert budget_approval.post_for_approval(record) == "m-1"
    # Then: the record carries the exact surface that message now lives on
    stored = _stored(drafts, record["id"])
    assert stored["message_id"] == "m-1"
    assert stored["kind"] == "budget-mail"
    assert stored["surface"] == "owner-dm"
    assert stored["channel_id"] == DM_CHANNEL
    assert stored["policy_version"] == POLICY_VERSION


def test_a_stale_writer_cannot_drop_the_stored_binding(
    budget_env: tuple[FakeDiscord, list[str], Path],
) -> None:
    # Given: a posted draft, plus the pre-post in-memory record that knows no binding
    _fake, _calls, drafts = budget_env
    record = _draft()
    assert budget_approval.post_for_approval(record) == "m-1"
    # When: a later caller re-binds the same message id from that stale record
    budget_gate.set_message_id(record, "m-1")
    # Then: the stored binding survives, so the owner's ✅ stays findable
    assert _stored(drafts, record["id"])["channel_id"] == DM_CHANNEL
    assert _stored(drafts, record["id"])["policy_version"] == POLICY_VERSION
