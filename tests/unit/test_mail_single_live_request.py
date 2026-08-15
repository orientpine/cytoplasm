"""EXACTLY ONE live owner-approval message per ``mail:{kind}:{uid}``.

The draft field bound here is ``message_id`` — the DISCORD approval message id,
written only by the gate's commit. It is NOT the RFC 5322 ``Message-ID`` of the
mail being answered: that header never reaches a draft record (the answered mail
is identified by ``uid`` / ``uid_opaque``), so the two can never be confused.
"""
from __future__ import annotations

import argparse
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
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import triage_approval  # noqa: E402
import triage_cli  # noqa: E402
import triage_confirm  # noqa: E402
import triage_gate  # noqa: E402
import triage_mode  # noqa: E402

OWNER = "owner-mail"
DM_CHANNEL = "100000000000000002"
# IPC 대기 상한 — 불변식이 아니라 hang 방지용이다. 직렬화 판정은 결과 단언(정확히 1 POST +
# 나머지 defer)이 한다.
#
# 이 값을 10→60으로 올린 것은 한때 간헐 실패를 '느린 러너의 스케줄링 지연'으로
# 진단했기 때문인데, 그 진단은 **틀렸다**. 진짜 원인은 비원자적 `write_json` 이어서,
# 패자가 쓰는 중인 레코드를 읽고 JSONDecodeError 로 죽어 결과를 안 썼다(그래서 메인의
# select 가 빈 목록으로 만료됐다). 원인은 tests/unit/test_gate_write_json_atomic.py 가
# 고정하고 있으며, 이 상한은 그저 넘넘한 hang 경계로 남겨둔다.
TIMEOUT_S = 60


class FakeDiscord:
    """Offline owner-DM log; ``log_path`` collects posts from every forked process."""

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
            return {"type": 1, "name": "", "recipients": [{"id": OWNER}]}
        if method == "POST" and parts[-1] == "messages":
            self.posts += 1
            message_id = f"m-{self.posts}"
            self.contents[message_id] = str((payload or {}).get("content", ""))
            self.calls.append(f"POST:{message_id}")
            if self.log_path is not None:
                with self.log_path.open("a", encoding="utf-8") as handle:
                    handle.write(message_id + "\n")
            return {"id": message_id}
        message_id = parts[3] if len(parts) > 3 else ""
        if method == "PUT":
            return None
        if method == "DELETE":
            self.contents.pop(message_id, None)
            self.calls.append(f"DELETE:{message_id}")
            return None
        if method == "GET" and len(parts) > 5:
            emoji = unquote(parts[5].split("?", 1)[0])
            if emoji == triage_confirm.APPROVE_EMOJI and message_id in self.approved:
                return [{"id": OWNER, "bot": False}]
            return []
        if method == "GET":
            content = self.contents.get(message_id)
            if content is None:
                raise HTTPError("https://discord.invalid", 404, "missing", Message(), None)
            return {"id": message_id, "content": content}
        raise AssertionError(f"unexpected Discord call: {method} {path}")




@pytest.fixture
def mail_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[FakeDiscord, list[str], Path]:
    calls: list[str] = []
    fake = FakeDiscord(calls)
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "mail-gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail-home"))
    monkeypatch.setenv("TRIAGE_MAILON_PYTHON", "python3")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER)
    monkeypatch.setattr(triage_confirm, "_api", fake.api)
    return fake, calls, tmp_path / "mail-gate" / "drafts"


def _draft(uid: str = "u-1", body: str = "본문") -> dict:
    return triage_gate.create_draft(
        uid=uid, sender="발신자 <s@example.invalid>", mail_subject="문의",
        to="owner@example.invalid", subject="Re: 문의", body=body,
        sensitive=False, tags=(), category="important", flags=("reply_needed",),
    )


def _stored(drafts: Path, draft_id: str) -> dict:
    return json.loads((drafts / f"{draft_id}.json").read_text(encoding="utf-8"))


def _live(drafts: Path) -> list[str]:
    """Every draft that still holds a Discord approval message id."""
    bound: list[str] = []
    for path in sorted(drafts.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("message_id"):
            bound.append(str(record["message_id"]))
    return bound


def _log_writes(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    original = triage_gate.write_json

    def write(path: Path, record: dict) -> None:
        calls.append(f"WRITE:{Path(path).stem}")
        original(path, record)

    monkeypatch.setattr(triage_gate, "write_json", write)


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
    original, blocked = triage_approval._pending_drafts, False

    def blocking() -> tuple[tuple[Path, dict, str], ...]:
        nonlocal blocked
        if not blocked:
            blocked = True
            os.write(ready, b"1")
            _wait_byte(release)
        return original()

    triage_approval._pending_drafts = blocking
    verdict = triage_approval.request_approval(draft)
    reason = verdict.reason.value if verdict.reason is not None else "none"
    os.write(result, f"{verdict.outcome.value}|{reason}".encode())


def test_second_request_for_the_same_draft_and_hash_posts_nothing(
    mail_env: tuple[FakeDiscord, list[str], Path],
) -> None:
    # Given: one pending draft already bound to a live owner-DM message
    fake, _calls, drafts = mail_env
    record = _draft()
    assert triage_approval.post_for_approval(record) == "m-1"
    # When: the same draft reaches the producer again with an unchanged hash
    reused = triage_approval.post_for_approval(record)
    # Then: nothing is posted and the stored message id is untouched
    assert reused == "m-1"
    assert fake.posts == 1
    assert _stored(drafts, record["id"])["message_id"] == "m-1"
    assert _live(drafts) == ["m-1"]


def test_changed_content_deletes_the_message_before_dropping_the_record(
    mail_env: tuple[FakeDiscord, list[str], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a live approval message for one uid and a second draft of new content
    fake, calls, drafts = mail_env
    stale = _draft(body="예전 본문")
    assert triage_approval.post_for_approval(stale) == "m-1"
    fresh = _draft(body="새 본문")
    _log_writes(monkeypatch, calls)
    # When: the producer requests approval for the new content on the same key
    assert triage_approval.post_for_approval(fresh) == "m-2"
    # Then: the stale message dies BEFORE its record is unbound, then one post happens
    assert calls.index("DELETE:m-1") < calls.index(f"WRITE:{stale['id']}")
    assert calls.index(f"WRITE:{stale['id']}") < calls.index("POST:m-2")
    assert fake.posts == 2
    assert _live(drafts) == ["m-2"]
    assert _stored(drafts, stale["id"])["message_id"] == ""


def test_owner_approved_request_is_deferred_without_deleting_or_touching_it(
    mail_env: tuple[FakeDiscord, list[str], Path],
) -> None:
    # Given: a live approval message the owner has already ✅
    fake, calls, drafts = mail_env
    record = _draft()
    assert triage_approval.post_for_approval(record) == "m-1"
    fake.approved.add("m-1")
    before = (drafts / f"{record['id']}.json").read_bytes()
    # When: the producer runs again for the same key
    with pytest.raises(triage_gate.GateError) as refusal:
        triage_approval.post_for_approval(_draft(body="새 본문"))
    # Then: the owner's decision survives — no delete, no repost, no record change
    assert refusal.value.exit_code == 1
    assert "DELETE:m-1" not in calls
    assert fake.posts == 1
    assert (drafts / f"{record['id']}.json").read_bytes() == before


def test_corrupt_draft_record_refuses_the_request_without_posting(
    mail_env: tuple[FakeDiscord, list[str], Path],
) -> None:
    # Given: an unreadable draft file beside a fresh, unposted draft
    fake, _calls, drafts = mail_env
    record = _draft()
    (drafts / "bad.json").write_text("{not-json\n", encoding="utf-8")
    # When: the producer asks for owner approval
    with pytest.raises(triage_gate.GateError) as refusal:
        triage_approval.post_for_approval(record)
    # Then: it refuses as a store problem and posts nothing
    assert refusal.value.exit_code == 3
    assert fake.posts == 0
    assert _stored(drafts, record["id"])["message_id"] == ""


def test_two_concurrent_producers_post_exactly_once(
    mail_env: tuple[FakeDiscord, list[str], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one unposted draft and two real producer processes racing for its key
    _fake, _calls, drafts = mail_env
    record = _draft()
    log = tmp_path / "posts.log"
    monkeypatch.setattr(triage_confirm, "_api", FakeDiscord([], log).api)
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
    assert len(_live(drafts)) == 1


def test_has_draft_for_still_short_circuits_a_second_draft_for_the_same_uid(
    mail_env: tuple[FakeDiscord, list[str], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: one existing draft for u-1 (the legacy pre-façade guard's subject)
    fake, _calls, _drafts = mail_env
    _draft(uid="u-1")
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    # When: the owner-instruction path is asked for a second draft on that uid
    with pytest.raises(triage_gate.GateError) as refusal:
        triage_cli.cmd_draft(
            argparse.Namespace(uid="u-1", instruction="회신해줘", attachment=[], no_post=False)
        )
    # Then: it still refuses before any mail read or approval post
    assert refusal.value.exit_code == 2
    assert triage_gate.has_draft_for("u-1") is True
    assert fake.posts == 0
