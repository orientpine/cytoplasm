"""발송 후 소유자 DM은 승인 provenance로 게이트된다 (allowlist, fail-closed).

scenario.sh §9는 조작된 owner id로 서명 주입 승인을 만들어 `confirm`을 돌린다 —
그 경로가 실제 Discord DM을 열면 배포 노드의 진짜 봇 토큰이 외부효과를 낸다.
소유자 리액션 승인만 알림 가능하며, 그 외(주입/미래 신규 방식)는 거부된다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import triage_cli  # noqa: E402


def _draft(draft_id: str = "mail-notify-1") -> dict:
    return {
        "id": draft_id,
        "subject": "회신 초안",
        "to": "peer@example.org",
        "status": "pending",
        "message_id": "msg-1",
    }


def test_notify_sent_when_approval_is_a_manual_reaction_then_dms_the_owner_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a committed send whose approval was the owner's own ✅ reaction.
    draft = _draft()
    notified: list[str] = []
    monkeypatch.setattr(
        triage_cli.triage_confirm, "dm_owner", lambda content: notified.append(content)
    )

    # When: the post-send notification runs with that provenance.
    triage_cli._notify_sent(draft, "manual_reaction")

    # Then: production notify stays healthy — exactly one owner DM.
    assert len(notified) == 1


def test_notify_sent_when_approval_is_signed_injection_then_makes_no_external_call(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an E2E/sandbox approval injected with a fabricated owner id.
    draft = _draft("mail-notify-injected")
    notified: list[str] = []
    monkeypatch.setattr(
        triage_cli.triage_confirm, "dm_owner", lambda content: notified.append(content)
    )

    # When: the post-send notification runs with that provenance.
    triage_cli._notify_sent(draft, "signed_injection_e2e")

    # Then: no Discord DM channel is opened, and the skip is auditable on stderr only.
    captured = capsys.readouterr()
    assert notified == []
    assert "NOTIFY-SKIP" in captured.err
    assert "mail-notify-injected" in captured.err
    assert "signed_injection_e2e" in captured.err


def test_notify_sent_when_approval_method_is_unknown_then_skips_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a provenance the guard has never heard of (a future approval method).
    draft = _draft("mail-notify-future")
    notified: list[str] = []
    monkeypatch.setattr(
        triage_cli.triage_confirm, "dm_owner", lambda content: notified.append(content)
    )

    # When: the post-send notification runs with that provenance.
    triage_cli._notify_sent(draft, "future_method_x")

    # Then: fail-closed — an allowlist refuses it; a denylist would have let it through.
    assert notified == []


def test_cmd_confirm_when_injection_approved_then_notifies_with_that_provenance(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: the scenario.sh §9 shape — a signed injection file approving a draft.
    draft = _draft("mail-notify-scenario")
    injection = tmp_path / "ok.json"
    injection.write_text("{}", encoding="utf-8")
    notified: list[str] = []
    monkeypatch.setattr(triage_cli.triage_gate, "load_draft", lambda _draft_id: draft)
    monkeypatch.setattr(
        triage_cli.triage_confirm, "confirm_via_injection", lambda _draft, _path: "injected"
    )
    monkeypatch.setattr(triage_cli.triage_confirm, "owner_id", lambda: "999900000000000625")
    monkeypatch.setattr(
        triage_cli.mail_preflight, "execute_cli_draft", lambda _draft, _approval: None
    )
    monkeypatch.setattr(
        triage_cli.triage_confirm, "dm_owner", lambda content: notified.append(content)
    )

    # When: the real confirm command sends under the injected approval.
    code = triage_cli.cmd_confirm(
        argparse.Namespace(draft="mail-notify-scenario", injection_file=str(injection))
    )

    # Then: the send still reports success on stdout (scenario.sh greps it), with no DM.
    captured = capsys.readouterr()
    assert notified == []
    assert code == 0
    assert "SENT draft=mail-notify-scenario method=signed_injection_e2e" in captured.out


def test_cmd_watch_when_owner_reacts_then_notifies_with_manual_reaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the production tick with one pending draft the owner has approved with ✅.
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    draft = _draft("mail-notify-watch")
    notified: list[str] = []
    monkeypatch.setattr(triage_cli.triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_cli.triage_confirm, "owner_id", lambda: "owner")
    monkeypatch.setattr(triage_cli.triage_gate, "list_drafts", lambda: [draft])
    monkeypatch.setattr(
        triage_cli.triage_confirm,
        "resolve_reaction",
        lambda _draft: triage_cli.triage_confirm.APPROVE_EMOJI,
    )
    monkeypatch.setattr(
        triage_cli.mail_preflight, "execute_cli_draft", lambda _draft, _approval: None
    )
    monkeypatch.setattr(
        triage_cli.triage_confirm, "dm_owner", lambda content: notified.append(content)
    )

    # When: the cron tick sends the approved draft.
    code = triage_cli.cmd_watch(argparse.Namespace())

    # Then: the owner-reaction path still notifies exactly once.
    assert code == 0
    assert len(notified) == 1
