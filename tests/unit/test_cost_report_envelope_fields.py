"""Complete periodic-report envelopes; keep compatibility/pipeline tests below their LOC ceiling."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from automation import owner_notice
from automation.interop.owner_message import OwnerMessage, Periodic, Result
from tests.unit.test_cost_report_periodic_notice import (
    COST_LEGACY,
    ROOT,
    TRENDS_LEGACY,
    Snapshot,
    cost as cost,
    envelopes as envelopes,
    snapshot as snapshot,
    trends,
)


@pytest.fixture
def messages(monkeypatch: pytest.MonkeyPatch) -> list[OwnerMessage]:
    """Observe the producer boundary without replacing facade rendering or its verdict."""
    captured: list[OwnerMessage] = []
    notify = owner_notice.notify_owner
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fixture-token")
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(ROOT))
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "111")
    monkeypatch.setattr(owner_notice, "send_notice", lambda token, channel, body: None)

    def forward(content: str, *, message: OwnerMessage | None = None) -> bool:
        assert message is not None
        captured.append(message)
        return notify(content, message=message)

    monkeypatch.setattr(owner_notice, "notify_owner", forward)
    return captured


def _assert_report_contract(message: OwnerMessage) -> None:
    assert message.contract_version == 1
    assert message.render_version == "owner-ko-v1"
    assert message.owner.verb == "none"
    assert message.owner.target is None
    assert message.owner.argument is None
    assert message.recovery == "not_applicable"
    assert message.location.space == "unknown"
    assert message.location.guild_id is None
    assert message.location.channel_id is None
    assert message.location.message_id is None
    assert message.location.url is None


def _assert_cost_identity(message: OwnerMessage) -> None:
    _assert_report_contract(message)
    assert message.subject_key == "cost-report"
    assert message.subject == "일일 지출"
    assert message.agent_next == "다음 정기 보고"
    assert message.location.scope == "resource"
    assert message.location.search == ("소유자용 원장 조회 화면 없음", "Discord 에이전트 DM에 '일일 지출 근거 확인' 요청")


def test_cost_report_fields_when_observation_time_exists(cost: ModuleType, messages: list[OwnerMessage]) -> None:
    # Given: independently captured legacy bytes and a fixed observation timestamp.
    # When: the real sender builds the envelope and the real facade delivers it.
    cost.send_dm(COST_LEGACY, kst_now="2026-09-07 09:00:00")
    # Then: every envelope field, including the kind and both endpoints, is pinned.
    message, = messages
    _assert_cost_identity(message)
    assert message.fact == (
        "📊 LiteLLM 일일 비용 리포트 — 2026-09-07 09:00:00 KST\n"
        "오늘(KST) 지출: $1.250000 (2건)\n이번 달 누적: $16.000000 (8건)\n"
        "전체 누적: $20.000000 (10건)\n키별(월): agent $16.000000 (8건)\n"
        "⚠️ 소프트캡 경보: 월 누적 $16.000000 — $15 소프트캡 초과\n"
        "(source: LiteLLM_SpendLogs · generated 2026-09-07T00:00:00Z)"
        " · 관측은 오늘 지출 기준; 월·전체 누적은 별도"
    )
    assert isinstance(message.detail, Periodic)
    assert message.detail.start.isoformat() == "2026-09-07T00:00:00+09:00"
    assert message.detail.end.isoformat() == "2026-09-07T09:00:00+09:00"


@pytest.mark.parametrize("stamp", ["", "?", "2026-02-30 09:00:00"])
def test_cost_report_fields_when_observation_time_unavailable(
    cost: ModuleType, messages: list[OwnerMessage], stamp: str,
) -> None:
    # Given: the same report bytes with missing or invalid observation metadata.
    # When
    cost.send_dm(COST_LEGACY, kst_now=stamp)
    # Then: an executed Result, not a cancelled or fabricated periodic report.
    message, = messages
    _assert_cost_identity(message)
    assert message.fact == (
        "📊 LiteLLM 일일 비용 리포트 — 2026-09-07 09:00:00 KST\n"
        "오늘(KST) 지출: $1.250000 (2건)\n이번 달 누적: $16.000000 (8건)\n"
        "전체 누적: $20.000000 (10건)\n키별(월): agent $16.000000 (8건)\n"
        "⚠️ 소프트캡 경보: 월 누적 $16.000000 — $15 소프트캡 초과\n"
        "(source: LiteLLM_SpendLogs · generated 2026-09-07T00:00:00Z)"
        " · 관측 구간 없음: KST 집계 시각 누락 또는 형식 오류"
    )
    assert isinstance(message.detail, Result)
    assert message.detail.outcome == "executed"


@pytest.mark.parametrize("has_path", [True, False])
def test_research_trends_fields_when_report_delivered(messages: list[OwnerMessage], has_path: bool) -> None:
    # Given: fixed report coordinates, independent of the checkout and pytest temp root.
    path = Path("reports/research-trends-20260907.md") if has_path else None
    # When
    trends._send_dm(TRENDS_LEGACY, report_path=path)
    # Then: absence of coordinates is distinct from the full searchable resource.
    message, = messages
    _assert_report_contract(message)
    assert message.subject_key == "research-trends"
    assert message.subject == "주간 연구 동향"
    assert message.agent_next == "다음 주 정기 보고"
    assert message.fact == (
        "📚 주간 연구 동향 — 2026-09-07 KST\n\n## topic\n요약\n논문 링크:\n"
        "- [Paper](https://example.org/paper) (2026-09-01)"
        " · 관측 구간 없음: 날짜 필터 없는 최신 검색"
    )
    assert isinstance(message.detail, Result)
    assert message.detail.outcome == "executed"
    if has_path:
        assert message.location.scope == "resource"
        assert message.location.search == ("연구동향 보고서", "research-trends-20260907.md")
    else:
        assert message.location.scope == "none"
        assert message.location.search is None


@pytest.mark.parametrize("stamp", ["2026-09-07 09:00:00", "?"])
def test_owner_location_when_no_ledger_view_exists(
    cost: ModuleType, snapshot: Snapshot, envelopes: list[str],
    monkeypatch: pytest.MonkeyPatch, stamp: str,
) -> None:
    # The literal is independent of both producer and renderer; parse the shipped field.
    snapshot["kst_now"] = stamp
    monkeypatch.setenv("COST_REPORT_SOFT_CAP", "15")
    monkeypatch.delenv("COST_REPORT_DRY_RUN", raising=False)
    monkeypatch.setattr(cost, "fetch_snapshot", lambda: snapshot)
    assert cost.main() == 0
    body, = envelopes
    fields = body.splitlines()
    assert len(fields) == 5
    location, = [line for line in fields if line.startswith("위치:")]
    assert location == (
        "위치: 링크 없음 (주소 없음); 검색: 소유자용 원장 조회 화면 없음 / "
        "Discord 에이전트 DM에 '일일 지출 근거 확인' 요청"
    )


def test_ops_detail_stays_out_of_owner_report(
    cost: ModuleType, envelopes: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exercise the real full-to-masked snapshot boundary, not a pre-masked-only mock.
    spec = importlib.util.spec_from_file_location(
        "cost_snapshot_owner_location", ROOT / "automation/cost-report/spend_snapshot.py",
    )
    assert spec is not None and spec.loader is not None
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)
    monkeypatch.setattr(source, "load_node_config", lambda: SimpleNamespace(primary_node_name="private-node"))
    monkeypatch.setenv("COST_REPORT_FIXTURE", "private-fixture-detail")

    def query(sql: str) -> list[list[str]]:
        if sql.startswith("SELECT to_char"):
            return [["2026-09-07 09:00:00"]]
        if sql.startswith("SELECT COALESCE"):
            return [["private-key-alias", "2", "1.25"]]
        assert sql.startswith("SELECT count(*)")
        return [["2", "1.25"]]

    monkeypatch.setattr(source, "psql", query)
    full, masked = source.build_snapshots()
    full_json = json.dumps(full)
    assert "private-fixture-detail" in full_json
    assert "private-key-alias" in full_json
    assert "private-node" in full_json
    assert masked["per_key_month_kst"] == [{"alias": "other", "requests": 2, "spend_usd": 1.25}]
    monkeypatch.setenv("COST_REPORT_SOFT_CAP", "15")
    monkeypatch.delenv("COST_REPORT_DRY_RUN", raising=False)
    monkeypatch.setattr(cost, "fetch_snapshot", lambda: masked)
    assert cost.main() == 0
    body, = envelopes
    for forbidden in (
        "private-fixture-detail", "private-key-alias", "private-node",
        "/srv/autophagy-private", "runtime-logs", "autophagy-spend-ro",
        "ops@", "DATABASE_URL", "api_key", "per_key_all_time",
    ):
        assert forbidden not in json.dumps(masked)
        assert forbidden not in body
    assert "other $1.250000 (2건)" in body
