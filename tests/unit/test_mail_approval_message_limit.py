"""승인 메시지가 Discord 한도를 넘을 때 posting journal 이 막히지 않아야 한다 (t_82644d12)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Never

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import triage_approval  # noqa: E402
import triage_gate  # noqa: E402


def _oversize_compose_draft() -> dict:
    return {
        "id": "d-1",
        "kind": "compose",
        "uid": "u-1",
        "to": "x@y.z",
        "subject": "긴 본문 발송",
        "body": "가" * 3000,
        "sha256": "sha256:draft",
    }


def test_an_oversize_approval_is_refused_before_the_journal_reserves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게시할 수 없는 메시지로 저널 키를 예약하면 그 초안은 영영 되살릴 수 없다.

    lifecycle 은 POST 전에 posting journal 을 예약하고, mail 은 그 예약을 message id 로
    보강하지 않는다. 그래서 Discord 가 400 을 주면 예약만 남아 이후 모든 재시도가
    POSTING_JOURNAL_STALE 로 거부됐다(t_82644d12). 한도 초과는 lifecycle 에 들어가기
    **전에** 거부되어야 한다.
    """
    def _never_entered() -> Never:
        raise AssertionError("lifecycle must not be entered for an unpostable message")

    monkeypatch.setattr(triage_approval, "lifecycle", _never_entered)

    with pytest.raises(triage_gate.GateError) as raised:
        _ = triage_approval.request_approval(_oversize_compose_draft())

    assert "한도" in str(raised.value)


def test_a_normal_approval_still_reaches_the_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """한도 안의 초안은 예전과 똑같이 lifecycle 로 간다 — 가드는 길이만 본다."""
    entered: list[str] = []

    class _Facade:
        @staticmethod
        def request_owner_approval(*args: object, **kwargs: object) -> str:
            entered.append("yes")
            return "verdict"

    monkeypatch.setattr(triage_approval, "lifecycle", lambda: _Facade)
    monkeypatch.setattr(triage_approval, "confirm_intent", lambda draft: object())
    monkeypatch.setattr(triage_approval, "confirm_lease", lambda: object())
    monkeypatch.setattr(triage_approval, "posting_journal", lambda: object())
    monkeypatch.setattr(triage_approval, "MailApprovalGate", lambda draft, notice="": object())

    draft = _oversize_compose_draft() | {"body": "짧은 본문"}
    assert triage_approval.request_approval(draft) == "verdict"
    assert entered == ["yes"]
