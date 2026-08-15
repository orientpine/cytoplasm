"""What a supply-chain watcher decides, before it is allowed to do anything.

FA-3. This is the piece that makes ✅ final: today nobody re-runs the pipeline when the
owner reacts, so an approval sits unread until a human tells a session about it. A
watcher closes that — and a watcher on the skill-mount path is the highest-privilege
automation in the system, so its decisions are separated from its effects and pinned
here first.

The vocabulary is deliberately three words. ``resume`` re-invokes the EXISTING pipeline,
which already re-verifies everything; there is no "mount" action, because a watcher that
could mount would be a second, weaker copy of the gate. ``retire`` is for a decision the
owner already made. ``retain`` is the answer to every uncertainty — unsupported kind,
unreadable reaction, transport failure — because the cost of retaining is one more tick
and the cost of guessing is a deploy nobody authorised.

Notably absent: any action that posts. Re-posting or superseding a request is what the
승인 메시지 단일성 규칙 forbids, and the way to guarantee a watcher never does it is to
give it no word for it.
"""
from __future__ import annotations

import pytest

from automation.supply_chain_plan import (
    SUPPORTED_KINDS,
    PendingRequest,
    plan_tick,
)

_DEPLOY = PendingRequest(
    key="skill-deploy:demo", kind="skill-deploy", name="demo", record_name="demo"
)
# A publish request's record file is NOT its skill name — that asymmetry is the reason
# `record_name` is carried rather than re-derived.
_PUBLISH = PendingRequest(
    key="skill-publish:demo", kind="skill-publish", name="demo", record_name="publish-demo"
)
_ATTEST = PendingRequest(
    key="skill-attest:demo", kind="skill-attest", name="demo", record_name="demo"
)


def _decide(answer: str):
    def decide(_request: PendingRequest) -> str:
        return answer

    return decide


def _actions(plans) -> list[str]:
    return [plan.action for plan in plans]


def test_an_approved_request_is_resumed() -> None:
    assert _actions(plan_tick((_DEPLOY,), decide=_decide("approved"))) == ["resume"]


def test_a_denied_request_is_settled_not_resumed() -> None:
    """The owner answered. Retrying would be arguing with them — but nothing is deleted.

    Measured 2026-08-01: no primitive means "the owner said no". ``consume`` retires the
    decision a MOUNT consumed, and a denial mounted nothing; ``abandon`` is an operator
    override whose audit records ``SUDO_USER`` as the authority, so a watcher calling it
    would attribute the act to a human who did nothing.

    None is needed. The pending record IS the durable refusal: the stop reaction takes
    precedence forever, and a content change supersedes the record on its own because
    the digest is a hash input. Retiring it would post a NEW request for the same bytes
    and ask the owner to decide again what they already decided.
    """
    plans = plan_tick((_DEPLOY,), decide=_decide("denied"))
    assert _actions(plans) == ["settled"]


def test_an_unanswered_request_is_retained() -> None:
    plans = plan_tick((_DEPLOY,), decide=_decide("absent"))
    assert _actions(plans) == ["retain"]


def test_an_undecidable_request_is_retained_never_resumed() -> None:
    """A transport failure is not permission — it is the absence of an answer."""

    def explode(_request: PendingRequest) -> str:
        raise RuntimeError("discord unreachable")

    plans = plan_tick((_DEPLOY,), decide=explode)
    assert _actions(plans) == ["retain"]
    assert "undecidable" in plans[0].reason


def test_an_unsupported_kind_is_retained_even_when_approved() -> None:
    """An adapter that does not exist must not be improvised by the watcher."""
    plans = plan_tick((_ATTEST,), decide=_decide("approved"))
    assert _actions(plans) == ["retain"]
    assert "unsupported" in plans[0].reason


def test_peer_attestation_is_not_an_owner_decision_surface() -> None:
    """It is a peer bot posting a verdict, not cha approving something."""
    assert "skill-attest" not in SUPPORTED_KINDS


def test_only_skill_deploy_is_resumable_from_a_record_alone() -> None:
    """Measured (2026-08-01): the other two need context the record does not carry.

    * ``managed-activate`` needs ``--activate-managed <quarantine-dir>``, and that
      directory must hold manifest.json, provenance.json and <skill>/SKILL.md. It is
      produced by the managed-sync fetch/verify step and its path appears nowhere in
      the pending record.
    * ``skill-publish`` resumes through a different program entirely
      (``managed_skills/publish_cli.py``), which requires ``--managed-repo``.

    Listing them as supported would promise an adapter that cannot be written yet, and
    the watcher would then invoke something with invented arguments. Retaining them is
    the honest state: the request stays live and a human can still finish it.
    """
    assert SUPPORTED_KINDS == frozenset({"skill-deploy"})


def test_each_request_yields_exactly_one_plan() -> None:
    """Two plans for one request would mean two invocations of the same effect."""
    plans = plan_tick((_DEPLOY, _PUBLISH), decide=_decide("approved"))
    assert len(plans) == 2
    assert len({plan.request.key for plan in plans}) == 2


def test_there_is_no_action_that_posts_or_mounts() -> None:
    """The absence of the word is the guarantee."""
    every = {
        plan.action
        for answer in ("approved", "denied", "absent")
        for plan in plan_tick((_DEPLOY, _PUBLISH), decide=_decide(answer))
    }
    assert every <= {"resume", "settled", "retain"}


def test_an_empty_tick_is_not_an_error() -> None:
    assert plan_tick((), decide=_decide("approved")) == ()


@pytest.mark.parametrize("answer", ["approve", "APPROVED", "", "ok", "yes"])
def test_an_unrecognised_decision_is_retained(answer: str) -> None:
    """Only the exact vocabulary counts; anything else is an answer we cannot read."""
    plans = plan_tick((_DEPLOY,), decide=_decide(answer))
    assert _actions(plans) == ["retain"]


def test_a_missing_approval_message_is_settled_under_its_own_name() -> None:
    """승인 메시지가 사라진 요청은 '아직 답 안 함'과 같은 칸에 들어가면 안 된다.

    조치가 다르기 때문이다 — unanswered 는 소유자를 기다리면 되지만, missing 은
    누를 대상 자체가 없어 사람이 재게시하기 전에는 영원히 움직이지 않는다. 같은
    reason 으로 보고되면 저널만 보고는 그 둘을 구분할 수 없고, 실제로 repair·topics
    두 건이 그렇게 묻혔다(2026-08-03).

    삭제된 메시지는 같은 레코드를 다시 폴링해도 달라지지 않는 종단 상태다. 이
    모듈에는 게시하거나 삭제할 단어가 없으므로 레코드는 보존하되 settled 로 분류해
    transient 재시도와 구분한다. 재게시는 소유자가 기존 파이프라인으로 결정한다.
    """
    plans = plan_tick((_DEPLOY,), decide=_decide("missing"))
    assert [(plan.action, plan.reason) for plan in plans] == [
        ("settled", "approval-message-missing")
    ]
