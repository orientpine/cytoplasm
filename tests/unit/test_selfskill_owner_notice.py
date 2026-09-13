"""Audit/shadow notice compatibility; independent base literals pin shipped bytes."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from automation import owner_notice, supply_chain_shadow_watch as shadow
from automation.interop import owner_message
from automation.interop.owner_message import Action, OwnerMessage, Ref, Result
from automation.selfskill_audit import ledger, report
from automation.selfskill_audit.claims import ApprovalClaimHit
from automation.selfskill_audit.overlap import OverlapHit
from automation.supply_chain_shadow_watch import plan_shadow_notice

AUDIT_EMPTY: Final = "[자체 스킬 감사] 계정=agent\n변경=0 "
AUDIT_ADVISORY: Final = (
    "[자체 스킬 감사] 계정=agent\n변경=0 \n"
    "SHADOWS-GOVERNED mail - 이 이름은 배포본을 가린다; 확인 후 archive/rename\n"
    "OVERLAPS-GOVERNED:recall 자가 스킬 mem-search 기능 겹침"
    "(score=0.62, 겹친 낱말: rag 검색 출처) - archive 하거나 repo 로 승격(코드화→PR→릴리스)\n"
    "CLAIMS-APPROVAL-ROLE:review [release] DO-NOT-APPROVE - "
    "approval review 절차를 archive 하거나 governed 코드로 승격"
)
SHADOW: Final = (
    "SHADOWS-GOVERNED mail — 자가 스킬이 배포본 이름을 가린다"
    " (승인 게이트를 강제하는 구현이 가려짐). `hermes curator archive <name>`"
    " 또는 이름 변경 후 배포본 발견을 확인하세요."
)


def test_legacy_bytes_when_audit_is_empty() -> None:
    # Given: the base's zero-delta report.
    # When
    content = report.render_summary((), account_label="agent")
    # Then
    assert content == AUDIT_EMPTY


def test_legacy_bytes_when_all_advisories_are_present() -> None:
    # Given
    hit = OverlapHit("mem-search", "recall", 0.62, ("rag", "검색", "출처"))
    claim = ApprovalClaimHit("review", ("[release]", "DO-NOT-APPROVE"))
    # When
    content = report.render_summary(
        (), account_label="agent", shadowed=("mail",), overlaps=(hit,), approval_claims=(claim,),
    )
    # Then
    assert content == AUDIT_ADVISORY


def test_legacy_bytes_when_a_shadow_is_new() -> None:
    # Given: an unseen governed name.
    # When
    plan = plan_shadow_notice(("mail",), ())
    # Then
    assert plan.notice == SHADOW


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    bodies: list[str] = []
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fixture-token")
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "111")
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", "222")
    def send(_token: str, _channel: str, body: str) -> None:
        bodies.append(body)

    monkeypatch.setattr(owner_notice, "send_notice", send)
    return bodies


@pytest.fixture
def audit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    skill = home / ".hermes" / "skills" / "mail"
    skill.mkdir(parents=True)
    _ = (skill / "SKILL.md").write_text("---\nname: mail\n---\nfixture\n", encoding="utf-8")
    root = tmp_path / "live"
    (root / "mail").mkdir(parents=True)
    monkeypatch.setattr(report, "_governed_root", lambda: root)
    monkeypatch.setattr(shadow, "_governed_root", lambda: root)
    monkeypatch.setenv("HERMES_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("SUPPLY_CHAIN_SHADOW_STATE", str(tmp_path / "shadows.json"))
    return home


def test_daily_notice_when_zero_delta_still_has_a_shadow(
    audit_home: Path, posted: list[str],
) -> None:
    # Given: the existing skill is already reported, but still shadows a governed name.
    now = datetime(2026, 9, 5, tzinfo=UTC)
    ledger.mark_reported(ledger.audit(audit_home, now=now))
    # When
    result = report.run_once(home=audit_home, account_label="agent", now=now)
    # Then
    assert result == 0
    assert len(posted) == 1
    assert "SHADOWS-GOVERNED mail" in posted[0]
    log = audit_home.parent / "state" / "logs" / "selfskill-audit" / "2026-09.jsonl"
    assert json.loads(log.read_text(encoding="utf-8"))["delta_counts"] == {
        "archived": 0, "created": 0, "edited": 0, "removed": 0, "restored": 0,
    }
    assert json.loads(log.read_text(encoding="utf-8"))["notified"] is True


def test_watermark_when_delivery_fails(
    audit_home: Path, monkeypatch: pytest.MonkeyPatch, posted: list[str],
) -> None:
    # Given: a pending ledger entry and a rejecting transport.
    now = datetime(2026, 9, 5, tzinfo=UTC)
    pending = ledger.audit(audit_home, now=now)

    def reject(_token: str, _channel: str, _body: str) -> None:
        raise OSError("fixture delivery failure")

    monkeypatch.setattr(owner_notice, "send_notice", reject)
    # When
    result = report.run_once(home=audit_home, account_label="agent", now=now)
    # Then
    assert result == 1
    assert json.loads(pending.state_path.read_text(encoding="utf-8"))["reported_lines"] == 0
    assert posted == []


@pytest.mark.parametrize("producer", ["audit", "shadow"])
def test_envelope_when_runtime_supports_it(
    producer: str, audit_home: Path, monkeypatch: pytest.MonkeyPatch, posted: list[str],
) -> None:
    # Given: the real facade and renderer, with only outbound delivery replaced.
    messages: list[OwnerMessage] = []
    render = owner_message.render

    def capture(message: OwnerMessage, *, destination: Ref) -> str:
        messages.append(message)
        return render(message, destination=destination)

    monkeypatch.setattr(owner_message, "render", capture)
    monkeypatch.chdir(audit_home.parent)
    # When
    if producer == "audit":
        _ = report.send_report((), account_label="agent")
    else:
        _ = shadow.run_shadow_check(home=Path("home"))
    # Then: an envelope reaches the real facade, not merely a discarded builder.
    assert len(messages) == 1
    (message,) = messages
    assert message.detail == Result(outcome="executed")
    assert message.subject_key == ("agent" if producer == "audit" else "mail")
    assert message.owner == Action(verb="none", target=None, argument=None)
    assert message.recovery == "not_applicable"
    if producer == "audit":
        assert message.subject == "[자체 스킬 감사]"
        assert message.fact == "변경=0 "
        assert message.agent_next == "다음 감사에서 재확인"
        assert message.location == Ref(
            scope="none", space="unknown", guild_id=None, channel_id=None,
            message_id=None, url=None, search=None,
        )
        assert posted == [
            "대상: [자체 스킬 감사] (agent)\n"
            + "사실: 변경=0 (실행 완료)\n"
            + "위치: 해당 없음\n"
            + "인계: 소유자: 조치 없음; 다음: 다음 감사에서 재확인\n"
            + "되돌리기: 해당 없음"
        ]
    else:
        assert message.subject == "자가 스킬 이름 가림"
        assert message.fact == SHADOW
        assert message.agent_next == "다음 틱에서 이름 대조"
        assert message.location == Ref(
            scope="resource", space="unknown", guild_id=None, channel_id=None,
            message_id=None, url=None, search=("자가 스킬 루트", "home/.hermes/skills"),
        )
        assert posted == [
            "대상: 자가 스킬 이름 가림 (mail)\n"
            + "사실: SHADOWS-GOVERNED mail — 자가 스킬이 배포본 이름을 가린다"
            + " (승인 게이트를 강제하는 구현이 가려짐). `hermes curator archive <name>`"
            + " 또는 이름 변경 후 배포본 발견을 확인하세요. (실행 완료)\n"
            + "위치: 링크 없음 (주소 없음); 검색: 자가 스킬 루트 / home/.hermes/skills\n"
            + "인계: 소유자: 조치 없음; 다음: 다음 틱에서 이름 대조\n"
            + "되돌리기: 해당 없음"
        ]


@pytest.mark.parametrize("producer", ["audit", "shadow"])
@pytest.mark.parametrize("missing", ["import", "capability"])
def test_legacy_delivery_when_runtime_is_old(
    producer: str, missing: str, audit_home: Path,
    monkeypatch: pytest.MonkeyPatch, posted: list[str],
) -> None:
    # Given: either the optional module cannot import, or the facade is pre-envelope.
    if missing == "import":
        monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    else:
        monkeypatch.delattr(owner_notice, "ACCEPTS_OWNER_MESSAGE")
    # When
    if producer == "audit":
        _ = report.send_report((), account_label="agent")
    else:
        _ = shadow.run_shadow_check(home=audit_home)
    # Then: independent base constants, including the audit's trailing space.
    assert posted == [AUDIT_EMPTY if producer == "audit" else SHADOW]


def test_advisories_when_delivered_in_an_envelope(
    posted: list[str],
) -> None:
    # Given
    hit = OverlapHit("mem-search", "recall", 0.62, ("rag", "검색", "출처"))
    claim = ApprovalClaimHit("review", ("[release]", "DO-NOT-APPROVE"))
    # When
    _ = report.send_report(
        (), account_label="agent", shadowed=("mail",), overlaps=(hit,), approval_claims=(claim,),
    )
    # Then: each machine advisory survives as its byte-identical sequence.
    assert len(posted) == 1
    assert len(posted[0].splitlines()) == 5
    for advisory in AUDIT_ADVISORY.splitlines()[2:]:
        assert advisory.encode() in posted[0].encode()


@pytest.mark.parametrize("delivered", [True, False])
@pytest.mark.usefixtures("posted")
def test_cli_notice_when_transport_accepts_or_rejects(
    delivered: bool, audit_home: Path,
) -> None:
    # Given: a real ledger; success exercises a zero-delta day with a persistent advisory.
    initial = ledger.audit(audit_home, now=datetime(2026, 9, 5, tzinfo=UTC))
    if delivered:
        ledger.mark_reported(initial)
    script = '''
import sys
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from unittest.mock import patch
from automation import owner_notice
from automation.selfskill_audit import report
home = Path(sys.argv[1])
def send(token, channel, body):
    if sys.argv[2] == "reject":
        raise OSError("fixture delivery failure")
    print(body)
run = partial(report.run_once, home=home, account_label="agent", now=datetime(2026, 9, 5, tzinfo=UTC))
with patch.object(owner_notice, "send_notice", send), patch.object(report, "_governed_root", lambda: home.parent / "live"), patch.object(report, "run_once", run):
    raise SystemExit(report.main(("--once",)))
'''
    # When: execute the actual CLI dispatcher in an isolated process, no network or clock.
    result = subprocess.run(
        [sys.executable, "-c", script, str(audit_home), "accept" if delivered else "reject"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    # Then: visible notice or nonzero exit, with only a successful send advancing the watermark.
    assert result.returncode == (0 if delivered else 1)
    if delivered:
        assert len(result.stdout.splitlines()) == 5
        assert "SHADOWS-GOVERNED mail" in result.stdout
    else:
        assert result.stdout == ""
        assert "NOTIFY-FAILED: OSError" in result.stderr
    assert json.loads(initial.state_path.read_text(encoding="utf-8"))["reported_lines"] == int(delivered)


def test_shadow_state_when_default_facade_delivery_fails(
    audit_home: Path, monkeypatch: pytest.MonkeyPatch, posted: list[str],
) -> None:
    # Given: an already-notified name and a different current shadow.
    state = shadow.state_path()
    _ = state.write_text('["wiki"]\n', encoding="utf-8")

    def reject(_token: str, _channel: str, _body: str) -> None:
        raise OSError("fixture delivery failure")

    monkeypatch.setattr(owner_notice, "send_notice", reject)
    # When
    current = shadow.run_shadow_check(home=audit_home)
    # Then: no state advancement on the envelope path either.
    assert current == ("mail",)
    assert state.read_text(encoding="utf-8") == '["wiki"]\n'
    assert posted == []
