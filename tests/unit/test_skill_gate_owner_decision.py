"""The deploy gate must honour ⛔, and must not confuse "denied" with "not answered yet".

FA-1. ``skill_gate_specs`` has declared ``CANCEL_EMOJI`` since the gate was written, and
AGENTS.md makes ⛔ precedence a repo-wide invariant for every owner-confirm flow:

    ⛔ 우선 — ✅와 ⛔가 함께 있으면 취소로 처리한다 (외부효과 fail-safe)

The deploy gate never implemented it. It queries the ✅ reaction endpoint and nothing
else, so an owner who approves and then changes their mind — the exact motion the ⛔
convention exists for — is silently overruled by their own earlier ✅. Every other
approval surface in the repo honours it; the skill supply chain, which is the highest
privilege path there is, did not.

The distinction between "denied" and "absent" is load-bearing rather than cosmetic:
a supply-chain reaction watcher (FA-3) has to retain a pending request that has not
been answered and retire one that was refused. Collapsing both into False would make
the watcher retry a decision the owner already made.
"""
from __future__ import annotations

import argparse
from typing import Any
from urllib.error import HTTPError

import pytest

from automation import skill_gate, skill_gate_specs

_OWNER = "111111111111111111"
_OTHER = "222222222222222222"
_CHANNEL = "333333333333333333"
_MESSAGE = "444444444444444444"


def _args() -> argparse.Namespace:
    return argparse.Namespace(message_id=_MESSAGE)


def _reactors(*users: tuple[str, bool]) -> list[dict[str, Any]]:
    return [{"id": user_id, "bot": is_bot} for user_id, is_bot in users]


def _stub_api(
    monkeypatch: pytest.MonkeyPatch,
    *,
    approve: list[dict[str, Any]] | Exception,
    deny: list[dict[str, Any]] | Exception,
) -> list[str]:
    """Route each reaction query by emoji so a test can answer them differently."""
    seen: list[str] = []

    def api(method: str, path: str, _payload: dict[str, str] | None = None) -> Any:
        seen.append(path)
        answer = deny if skill_gate_specs.CANCEL_EMOJI in path or "%E2%9B%94" in path else approve
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(skill_gate, "_api", api)
    return seen


def _http_error(code: int) -> HTTPError:
    return HTTPError("https://discord.invalid", code, "boom", {}, None)  # type: ignore[arg-type]


def test_deny_beats_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    """The owner reacted ✅ and then ⛔. The later refusal wins, per the repo convention."""
    _stub_api(monkeypatch, approve=_reactors((_OWNER, False)), deny=_reactors((_OWNER, False)))
    assert skill_gate._owner_decision(_args(), _OWNER, _CHANNEL) == "denied"
    assert skill_gate._owner_approval_present(_args(), _OWNER, _CHANNEL) is False


def test_only_deny_is_denied_not_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_api(monkeypatch, approve=[], deny=_reactors((_OWNER, False)))
    assert skill_gate._owner_decision(_args(), _OWNER, _CHANNEL) == "denied"


def test_only_approve_is_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_api(monkeypatch, approve=_reactors((_OWNER, False)), deny=[])
    assert skill_gate._owner_decision(_args(), _OWNER, _CHANNEL) == "approved"


def test_no_reaction_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_api(monkeypatch, approve=[], deny=[])
    assert skill_gate._owner_decision(_args(), _OWNER, _CHANNEL) == "absent"


def test_non_owner_deny_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anyone can react in the channel; only the owner decides."""
    _stub_api(monkeypatch, approve=_reactors((_OWNER, False)), deny=_reactors((_OTHER, False)))
    assert skill_gate._owner_decision(_args(), _OWNER, _CHANNEL) == "approved"


def test_bot_deny_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_api(monkeypatch, approve=_reactors((_OWNER, False)), deny=_reactors((_OWNER, True)))
    assert skill_gate._owner_decision(_args(), _OWNER, _CHANNEL) == "approved"


def test_an_unused_emoji_answers_empty_and_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """"아무도 그 이모지를 안 썼다"는 404 가 아니라 200 + [] 로 온다 — 2026-08-03 실측.

    이 파일은 원래 그 경우를 404 로 적어두고 있었는데 사실이 아니었다. 살아있는
    메시지에 아무도 안 쓴 이모지를 물으면 Discord 는 ``200 []`` 를 준다. 404 는
    ``10008 Unknown Message`` — 즉 "볼 수 없었다"뿐이다.
    """
    _stub_api(monkeypatch, approve=[], deny=[])
    assert skill_gate._owner_decision(_args(), _OWNER, _CHANNEL) == "absent"


def test_a_deleted_message_is_missing_not_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """승인 메시지가 사라진 것과 아직 안 누른 것은 다른 상태다.

    둘을 뭉개면 워처가 "아직 답 안 함"으로 영원히 보고하며 요청을 살려둔다. 소유자는
    누를 것이 없는데 시스템은 건강하다고 말한다 — 2026-08-03 실측으로 repair·topics
    두 건이 정확히 그 상태였고, 소유자가 알아차릴 때까지 아무 신호도 없었다.
    """
    _stub_api(monkeypatch, approve=_http_error(404), deny=_http_error(404))
    assert skill_gate._owner_decision(_args(), _OWNER, _CHANNEL) == "missing"


def test_a_gone_message_is_never_read_as_an_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    """404 를 "그 이모지는 아무도 안 씀"으로 삼키면 최고 권한 경로가 fail-open 된다.

    옛 구현은 ⛔ 조회가 404 여도 ✅ 조회 결과만 보고 approved 를 낼 수 있었다. 메시지가
    사라졌다면 어느 쪽도 확인된 것이 아니므로 승인이 될 수 없다.
    """
    _stub_api(monkeypatch, approve=_reactors((_OWNER, False)), deny=_http_error(404))
    assert skill_gate._owner_decision(_args(), _OWNER, _CHANNEL) == "missing"


def test_missing_keeps_the_reviewed_artifact_like_an_unanswered_request() -> None:
    """사라진 메시지의 복구는 재게시다 — 아티팩트를 버리는 denied 와 같으면 안 된다."""
    assert skill_gate.decision_exit_code("missing") == 1
    assert skill_gate.decision_exit_code("missing") != skill_gate.DENIED_EXIT


def test_transport_failure_on_the_deny_query_is_not_an_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unable to see whether the owner refused is not permission to proceed."""
    _stub_api(monkeypatch, approve=_reactors((_OWNER, False)), deny=_http_error(503))
    with pytest.raises(HTTPError):
        _ = skill_gate._owner_decision(_args(), _OWNER, _CHANNEL)


def test_the_deny_reaction_is_actually_queried(monkeypatch: pytest.MonkeyPatch) -> None:
    """The defect was not a wrong answer — it was never asking the question."""
    seen = _stub_api(monkeypatch, approve=_reactors((_OWNER, False)), deny=[])
    _ = skill_gate._owner_decision(_args(), _OWNER, _CHANNEL)
    assert any("%E2%9B%94" in path or skill_gate_specs.CANCEL_EMOJI in path for path in seen), seen


def test_denied_and_absent_are_different_exit_codes() -> None:
    """⛔ and "not answered yet" must not look the same to the caller.

    ``deploy-skill.sh`` treats exit 1 as "retry with a peer-attestation refresh", which
    is right for a request nobody has answered and wrong for one the owner refused: it
    spends a Discord round-trip re-attesting a deployment that has been cancelled, and
    then reports it as "approval ABSENT or INVALID", which is not what happened.

    The split is also what FA-2 needs. A resume path has to retain the reviewed
    artifact for a request still awaiting an answer and discard it for a refused one;
    one exit code cannot drive both.
    """
    assert skill_gate.decision_exit_code("approved") == 0
    assert skill_gate.decision_exit_code("absent") == 1
    assert skill_gate.decision_exit_code("denied") == skill_gate.DENIED_EXIT
    assert skill_gate.DENIED_EXIT not in (0, 1)


def test_the_denied_exit_code_does_not_collide_with_an_existing_one() -> None:
    """7 already means "peer attestation expired" to deploy-skill.sh."""
    assert skill_gate.DENIED_EXIT not in (0, 1, 2, 3, 4, 5, 7)
