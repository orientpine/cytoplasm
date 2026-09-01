"""승인 요청을 supersede 한 뒤 publish 가 실패해도 대기 건이 0 이 되지 않음을 고정한다.

실측 2026-08-29 13:52: 한 실행이 기존 제안 요청을 supersede 하고(메시지 delete + 레코드
drop) publish 성공에 도달하지 못했다. pending/ 은 비었고 proposals.jsonl 에는 새 행이
없었으며 소유자는 누를 것이 없었고 아무 신호도 나가지 않았다. 「승인 메시지 단일성 규칙」의
역방향 고아 — 단일성은 지켰지만 가용성을 잃었다.

이 파일은 기존 `test_approval_lifecycle*.py` 와 분리한다. 그 파일들은 성공 경로의 호출
순서(delete→drop→post→commit)와 저널 복구 표를 이미 고정하고 있고, 여기서 고정하는 것은
그 순서가 **깨진 뒤의 복구 계약**(restore + 마커 + 통지)이다. 별도 파일로 두어 기존 증거
파일에 한 줄도 더하지 않는다.

통지는 주입한다 — import 하지 않는다. `automation/owner_notice.py` 는 Discord 전송과
chunker 를 끌고 오는데 `deploy-skill.sh` 는 그 셋을 스테이징하지 않는다. 게이트가 스테이징
되지 않은 모듈을 import 하면 노드에서 ImportError 로 모든 배포가 닫힌다. 마지막 두 테스트가
그 경계를 고정한다.
"""
from __future__ import annotations

import ast
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from automation.interop.approval_lease import PostingJournal
from automation.interop.approval_lifecycle import (
    ApprovalIntent,
    ApprovalRequest,
    ApprovalSurfaceError,
    Outcome,
    PostedApproval,
    Probe,
    Reason,
    Verdict,
    request_owner_approval,
)

KEY = "drive:project/a"
INTENT = ApprovalIntent(KEY, "new", "channel")
MARKER_EVENT = "supersede-publish-failed"
FACADE = Path(__file__).resolve().parents[2] / "automation" / "interop" / "approval_lifecycle.py"


def _request(message_id: str, action_hash: str = "old") -> ApprovalRequest:
    return ApprovalRequest(KEY, action_hash, message_id, "channel", "2026-01-01T00:00:00Z")


class FakeLease:
    def __init__(self, owned: bool = True) -> None:
        self.owned = owned

    @contextmanager
    def hold(self, key: str) -> Iterator[bool]:
        del key
        yield self.owned


class PublishFailingGate:
    """A gate that supersedes normally and then fails to publish the replacement."""

    def __init__(
        self,
        records: tuple[ApprovalRequest, ...] = (),
        error: ApprovalSurfaceError | OSError | None = None,
    ) -> None:
        self.records = list(records)
        self.error: ApprovalSurfaceError | OSError | None = error
        self.calls: list[str] = []
        self.posts = 0

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        self.calls.append("outstanding")
        return tuple(record for record in self.records if record.key == key)

    def probe(self, request: ApprovalRequest) -> Probe:
        self.calls.append(f"probe:{request.message_id}")
        return Probe.BOUND_PENDING

    def delete(self, request: ApprovalRequest) -> None:
        self.calls.append(f"delete:{request.message_id}")

    def drop(self, request: ApprovalRequest) -> None:
        self.calls.append(f"drop:{request.message_id}")
        if request in self.records:
            self.records.remove(request)

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        self.calls.append("post")
        self.posts += 1
        if self.error is not None:
            raise self.error
        return PostedApproval(f"posted-{self.posts}", intent.channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        self.calls.append("commit")
        self.records.append(
            ApprovalRequest(intent.key, intent.action_hash, posted.message_id, posted.channel_id, created_at)
        )


class RestoringGate(PublishFailingGate):
    """The same gate plus the durable rollback seam a record store can offer."""

    def restore(self, request: ApprovalRequest) -> None:
        self.calls.append(f"restore:{request.message_id}")
        if request not in self.records:
            self.records.append(request)


def _markers(journal_root: Path) -> list[dict[str, object]]:
    """Every machine-readable failure marker the façade left under the journal root."""
    found: list[dict[str, object]] = []
    for path in sorted(journal_root.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                found.append(json.loads(line))
    return [entry for entry in found if entry.get("event") == MARKER_EVENT]


@pytest.fixture
def notices() -> list[str]:
    """The owner-notice sink a NON-gate caller wires in; the staged gate wires nothing."""
    return []


def _sink(delivered: list[str]) -> Callable[[str], bool]:
    def notifier(notice: str) -> bool:
        delivered.append(notice)
        return True

    return notifier


def _run(
    tmp_path: Path,
    gate: PublishFailingGate,
    notices: list[str] | None = None,
) -> Verdict:
    return request_owner_approval(
        INTENT,
        gate,
        FakeLease(),
        PostingJournal(tmp_path / "journal"),
        None if notices is None else _sink(notices),
    )


@pytest.mark.parametrize("error", [ApprovalSurfaceError("publish"), OSError("publish")])
def test_publish_failure_after_supersede_restores_the_superseded_record(
    tmp_path: Path,
    notices: list[str],
    error: ApprovalSurfaceError | OSError,
) -> None:
    # Given: one live owner request this run must supersede, and a surface that cannot publish.
    record = _request("m1")
    gate = RestoringGate((record,), error)

    # When: the façade supersedes the old request and then fails to publish the replacement.
    verdict = _run(tmp_path, gate, notices)

    # Then: the destroyed request is put back — the owner is never left with zero pending.
    assert gate.records == [record]
    assert (verdict.outcome, verdict.reason) == (Outcome.REFUSED, Reason.SUPERSEDE_FAILED)
    assert verdict.cleared == ()


@pytest.mark.parametrize("error", [ApprovalSurfaceError("publish"), OSError("publish")])
def test_publish_failure_after_supersede_is_loud(
    tmp_path: Path,
    notices: list[str],
    error: ApprovalSurfaceError | OSError,
) -> None:
    # Given: the same supersede-then-publish-failure, observed through the journal root.
    gate = RestoringGate((_request("m1"),), error)
    journal_root = tmp_path / "journal"

    # When: the critical section ends without a published request.
    _ = _run(tmp_path, gate, notices)

    # Then: one durable marker names the key and the destroyed message, and the owner is told.
    markers = _markers(journal_root)
    assert len(markers) == 1
    assert markers[0]["key"] == KEY
    assert markers[0]["superseded"] == ["m1"]
    assert markers[0]["restored"] == ["m1"]
    assert len(notices) == 1 and KEY in notices[0]


def test_publish_failure_after_supersede_without_restore_seam_still_signals(
    tmp_path: Path,
    notices: list[str],
) -> None:
    # Given: a record store with no rollback seam — restoration is impossible here.
    record = _request("m1")
    gate = PublishFailingGate((record,), ApprovalSurfaceError("publish"))

    # When: publishing fails after the request was already superseded.
    verdict = _run(tmp_path, gate, notices)

    # Then: the loss is reported, never swallowed — marker, notice, and a named lost record.
    assert (verdict.outcome, verdict.reason) == (Outcome.REFUSED, Reason.SUPERSEDE_FAILED)
    assert [item.request for item in verdict.cleared] == [record]
    markers = _markers(tmp_path / "journal")
    assert len(markers) == 1 and markers[0]["restored"] == [] and markers[0]["lost"] == ["m1"]
    assert len(notices) == 1


def test_publish_failure_after_supersede_preserves_delete_before_drop_and_reservation(
    tmp_path: Path,
    notices: list[str],
) -> None:
    # Given: the L2 ordering (delete the Discord message BEFORE dropping the record).
    gate = RestoringGate((_request("m1"),), ApprovalSurfaceError("publish"))
    journal = PostingJournal(tmp_path / "journal")

    # When: the publish that follows the supersede fails.
    verdict = _run(tmp_path, gate, notices)

    # Then: the ordering is unchanged, nothing is re-published, and the receipt stays fail-closed.
    assert gate.calls[:6] == ["outstanding", "probe:m1", "probe:m1", "delete:m1", "drop:m1", "post"]
    assert gate.posts == 1
    assert verdict.outcome is Outcome.REFUSED
    assert journal.outstanding(KEY) is not None


def test_successful_publish_leaves_no_marker_and_sends_no_notice(
    tmp_path: Path,
    notices: list[str],
) -> None:
    # Given: the ordinary supersede-then-publish path with a healthy surface.
    gate = RestoringGate((_request("m1"),))

    # When: the replacement request is published and committed.
    verdict = _run(tmp_path, gate, notices)

    # Then: the rescue path never fires — no marker, no notice, no restoration.
    assert verdict.outcome is Outcome.POSTED
    assert _markers(tmp_path / "journal") == [] and notices == []
    assert not any(call.startswith("restore") for call in gate.calls)


def test_publish_failure_without_a_supersede_keeps_propagating(
    tmp_path: Path,
    notices: list[str],
) -> None:
    # Given: an empty snapshot — this run destroys nothing, so there is nothing to lose.
    gate = RestoringGate((), ApprovalSurfaceError("publish"))

    # When: the first publish for the key fails.
    with pytest.raises(ApprovalSurfaceError):
        _ = _run(tmp_path, gate, notices)

    # Then: the pre-existing crash contract is untouched (the reservation wedges the key).
    assert _markers(tmp_path / "journal") == [] and notices == []


def test_staged_gate_without_a_notifier_still_gets_the_marker_and_the_restore(
    tmp_path: Path,
) -> None:
    # Given: the staged deploy gate, which wires NO notifier because it may import none.
    record = _request("m1")
    gate = RestoringGate((record,), ApprovalSurfaceError("publish"))

    # When: publish fails after the supersede with notifier=None (the default).
    verdict = _run(tmp_path, gate)

    # Then: the loss is still restored and still durably marked — only delivery is absent.
    assert gate.records == [record]
    assert (verdict.outcome, verdict.reason) == (Outcome.REFUSED, Reason.SUPERSEDE_FAILED)
    markers = _markers(tmp_path / "journal")
    assert len(markers) == 1 and markers[0]["notified"] is False
    assert markers[0]["restored"] == ["m1"]


def test_a_raising_notifier_cannot_break_the_rescue(tmp_path: Path) -> None:
    # Given: a wired notifier that throws — delivery must never gate recovery.
    record = _request("m1")
    gate = RestoringGate((record,), ApprovalSurfaceError("publish"))

    def exploding(notice: str) -> bool:
        del notice
        raise RuntimeError("notice transport down")

    # When: the rescue path attempts the owner notice.
    verdict = request_owner_approval(
        INTENT, gate, FakeLease(), PostingJournal(tmp_path / "journal"), exploding
    )

    # Then: restore and marker survive the failed delivery, recorded as not notified.
    assert gate.records == [record]
    assert verdict.reason is Reason.SUPERSEDE_FAILED
    markers = _markers(tmp_path / "journal")
    assert len(markers) == 1 and markers[0]["notified"] is False


def test_the_facade_imports_nothing_the_deploy_gate_does_not_stage() -> None:
    # Given: this module is copied onto deploy nodes by a FROZEN staging list.
    tree = ast.parse(FACADE.read_text(encoding="utf-8"), filename=str(FACADE))

    # When: every automation import in it is collected, at any nesting depth.
    imported = {
        name
        for node in ast.walk(tree)
        for name in (
            [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
        )
        + ([alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
        if name.startswith("automation.")
    }

    # Then: the owner-notice chain is reached by injection only — importing it would put
    # owner_notice + discord_transport + chunker in the staged gate's closure.
    assert "automation.owner_notice" not in imported
    assert not any("owner_notice" in name or "transport" in name for name in imported)
