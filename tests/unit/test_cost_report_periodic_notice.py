"""지출·동향 통지의 기존 바이트와 봉투 이관 경계를 고정한다."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Literal, TypeAlias, TypedDict, assert_never

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "automation/research_trends"))

from automation import owner_notice  # noqa: E402
from automation.knowledge.pack import EvidencePack, KnowledgeQuery  # noqa: E402
from automation.research_trends import research_trends_core as core  # noqa: E402

with pytest.MonkeyPatch.context() as import_env:
    import_env.setenv("TOPICS_SCRIPTS", str(ROOT / "skills/topics/scripts"))
    from automation.research_trends import research_trends as trends

COST_LEGACY = (
    "📊 LiteLLM 일일 비용 리포트 — 2026-09-07 09:00:00 KST\n"
    "오늘(KST) 지출: $1.250000 (2건)\n이번 달 누적: $16.000000 (8건)\n"
    "전체 누적: $20.000000 (10건)\n키별(월): agent $16.000000 (8건)\n"
    "⚠️ 소프트캡 경보: 월 누적 $16.000000 — $15 소프트캡 초과\n"
    "(source: LiteLLM_SpendLogs · generated 2026-09-07T00:00:00Z)"
)
TRENDS_LEGACY = (
    "📚 주간 연구 동향 — 2026-09-07 KST\n\n## topic\n요약\n논문 링크:\n"
    "- [Paper](https://example.org/paper) (2026-09-01)"
)


@pytest.fixture
def cost() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "cost_report_periodic", ROOT / "automation/cost-report/send_cost_report.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Spend(TypedDict):
    spend_usd: float
    requests: int


class KeySpend(Spend):
    alias: str


class Snapshot(TypedDict):
    kst_now: str | None
    generated_at_utc: str
    today_kst: Spend
    month_to_date_kst: Spend
    all_time: Spend
    per_key_month_kst: list[KeySpend]


Producer: TypeAlias = Literal["cost", "trends"]


@pytest.fixture
def snapshot() -> Snapshot:
    return {
        "kst_now": "2026-09-07 09:00:00", "generated_at_utc": "2026-09-07T00:00:00Z",
        "today_kst": {"spend_usd": 1.25, "requests": 2},
        "month_to_date_kst": {"spend_usd": 16, "requests": 8},
        "all_time": {"spend_usd": 20, "requests": 10},
        "per_key_month_kst": [{"alias": "agent", "spend_usd": 16, "requests": 8}],
    }


def test_cost_report_legacy_bytes_when_composed(cost: ModuleType, snapshot: Snapshot) -> None:
    # Given: a fixed snapshot. When: composed. Then: base bytes, independently captured.
    assert cost.compose(snapshot, 15) == COST_LEGACY


def test_research_trends_legacy_bytes_when_assembled() -> None:
    # Given
    outcomes = (core.TopicOutcome("topic", (
        core.Paper("Paper", "abstract", "https://example.org/paper", "2026-09-01"),
    ), "요약", None),)
    # When
    report = core.assemble_report("2026-09-07", outcomes)
    # Then
    assert report == TRENDS_LEGACY


@pytest.mark.parametrize("producer", ["cost", "trends"])
def test_legacy_bytes_when_facade_has_no_capability(cost: ModuleType, monkeypatch: pytest.MonkeyPatch, producer: Producer) -> None:
    # Given: an older facade exposes no capability and accepts only positional content.
    sent: list[str] = []
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fixture-token")
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(ROOT))
    monkeypatch.delattr(owner_notice, "ACCEPTS_OWNER_MESSAGE")
    monkeypatch.setattr(owner_notice, "notify_owner", lambda content: sent.append(content) or True)
    # When
    match producer:
        case "cost":
            cost.send_dm(COST_LEGACY)
            expected = COST_LEGACY
        case "trends":
            trends._send_dm(TRENDS_LEGACY)
            expected = TRENDS_LEGACY
        case unreachable:
            assert_never(unreachable)
    # Then
    assert sent == [expected]


@pytest.mark.parametrize("cap", [15, 16, 17])
def test_cost_report_soft_cap_when_at_boundary(cost: ModuleType, snapshot: Snapshot, cap: int) -> None:
    # Given: month spend is 16. When: composing with each threshold. Then: strict greater-than.
    report = cost.compose(snapshot, cap)
    assert ("⚠️" in report) is (16 > cap)


@pytest.mark.parametrize("producer", ["cost", "trends"])
def test_delivery_error_when_facade_returns_false(cost: ModuleType, monkeypatch: pytest.MonkeyPatch, producer: Producer) -> None:
    # Given
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fixture-token")
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(ROOT))
    monkeypatch.setattr(owner_notice, "notify_owner", lambda content, **kwargs: False)
    # When / Then: unsuccessful delivery remains a caller-visible error.
    with pytest.raises(RuntimeError, match="owner notice delivery failed"):
        match producer:
            case "cost":
                cost.send_dm(COST_LEGACY)
            case "trends":
                trends._send_dm(TRENDS_LEGACY)
            case unreachable:
                assert_never(unreachable)


@pytest.fixture
def envelopes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    # The real facade renders; only its final transport is replaced.
    sent: list[str] = []
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fixture-token")
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(ROOT))
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "111")
    monkeypatch.setattr(owner_notice, "send_notice", lambda token, channel, body: sent.append(body))
    return sent


def test_cost_report_observation_window_when_snapshot_has_kst(cost: ModuleType, snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch, envelopes: list[str]) -> None:
    # Given
    monkeypatch.setenv("COST_REPORT_SOFT_CAP", "15")
    monkeypatch.delenv("COST_REPORT_DRY_RUN", raising=False)
    monkeypatch.setattr(cost, "fetch_snapshot", lambda: snapshot)
    # When: the real runnable entry point composes and delivers.
    assert cost.main() == 0
    # Then: window is the existing today-KST aggregate, not the monthly/all-time totals.
    assert "관측: 2026-09-07T00:00:00+09:00 ~ 2026-09-07T09:00:00+09:00" in envelopes[0]
    assert len(envelopes[0].splitlines()) == 5
    assert "https://discord.com" not in envelopes[0]


@pytest.mark.parametrize("stamp", [None, "", "?", "2026-02-30 09:00:00"])
def test_cost_report_reason_when_window_unavailable(cost: ModuleType, snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch, envelopes: list[str], stamp: str | None) -> None:
    # Given: optional observation metadata is absent or malformed.
    snapshot["kst_now"] = stamp
    monkeypatch.setenv("COST_REPORT_SOFT_CAP", "15")
    monkeypatch.delenv("COST_REPORT_DRY_RUN", raising=False)
    monkeypatch.setattr(cost, "fetch_snapshot", lambda: snapshot)
    # When
    assert cost.main() == 0
    # Then: report is still delivered, without invented dates.
    assert "관측 구간 없음" in envelopes[0]
    assert "KST 집계 시각" in envelopes[0]
    assert "$16.000000" in envelopes[0]


def test_research_trends_reason_when_query_has_no_time_filter(envelopes: list[str]) -> None:
    # Given: latest-result queries have no observation interval.
    # When
    trends._send_dm(TRENDS_LEGACY)
    # Then: distinguish a report outcome from a fabricated weekly observation.
    assert "관측 구간 없음" in envelopes[0]
    assert "날짜 필터 없는 최신 검색" in envelopes[0]
    assert "https://example.org/paper" in envelopes[0]
    assert len(envelopes[0].splitlines()) == 5


@pytest.mark.parametrize("producer", ["cost", "trends"])
def test_legacy_bytes_when_owner_message_import_unavailable(cost: ModuleType, monkeypatch: pytest.MonkeyPatch, producer: Producer) -> None:
    # Given: mixed-generation runtime; import fails while the capable facade exists.
    sent: list[str] = []
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fixture-token")
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(ROOT))
    monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    monkeypatch.setattr(owner_notice, "notify_owner", lambda content: sent.append(content) or True)
    # When
    match producer:
        case "cost":
            cost.send_dm(COST_LEGACY)
            expected = COST_LEGACY
        case "trends":
            trends._send_dm(TRENDS_LEGACY)
            expected = TRENDS_LEGACY
        case unreachable:
            assert_never(unreachable)
    # Then
    assert sent == [expected]


@pytest.mark.parametrize("delivered", [True, False])
def test_research_trends_notice_when_running_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delivered: bool) -> None:
    # Given: fixed input and clock; real report assembly, persistence, facade and watermark.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("RESEARCH_TRENDS_STATE_DIR", "state")
    monkeypatch.setenv("RESEARCH_TRENDS_REPORT_DIR", "reports")
    monkeypatch.delenv("RESEARCH_TRENDS_DRY_RUN", raising=False)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fixture-token")
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "111")
    monkeypatch.setattr(trends, "datetime", SimpleNamespace(now=lambda tz: datetime.fromisoformat("2026-09-07T09:00:00+09:00")))
    monkeypatch.setattr(trends, "_safe_topics", lambda: ("topic",))
    pack = EvidencePack("knowledge-v1", KnowledgeQuery("topic"), "no_evidence", (), {})
    monkeypatch.setattr(trends.topics_knowledge, "collect", lambda topics: pack)
    monkeypatch.setattr(trends, "_fetch_all", lambda topic: ())
    monkeypatch.setattr(trends, "weekly_quality_section", lambda root: "")
    ingested: list[bool] = []
    monkeypatch.setattr(trends, "_ingest_report", lambda: ingested.append(True))
    sent: list[str] = []

    def transport(token: str, channel: str, body: str) -> None:
        if not delivered:
            raise OSError("fixture transport unavailable")
        sent.append(body)

    monkeypatch.setattr(owner_notice, "send_notice", transport)
    # When
    if delivered:
        assert trends.run() == 0
    else:
        with pytest.raises(trends.OwnerDmDeliveryError):
            trends.run()
    # Then: success alone consumes the week; a report search key uses no invented URL.
    assert trends._delivered_week() == ("2026-W37" if delivered else "")
    assert ingested == ([True] if delivered else [])
    if delivered:
        assert "research-trends-20260907.md" in sent[0]
        assert "https://discord.com" not in sent[0]
        assert Path("reports/research-trends-20260907.md").is_file()


@pytest.mark.parametrize("valid", [True, False])
def test_cost_report_cli_when_snapshot_is_supplied(tmp_path: Path, snapshot: Snapshot, valid: bool) -> None:
    # Given: real CLI, local snapshot, no SSH or delivery.
    source = tmp_path / "snapshot.json"
    source.write_text(json.dumps(snapshot if valid else {}), encoding="utf-8")
    env = {**os.environ, "COST_REPORT_SOFT_CAP": "15", "COST_REPORT_DRY_RUN": "1",
           "COST_REPORT_SNAPSHOT_FILE": str(source)}
    # When
    completed = subprocess.run(
        [sys.executable, str(ROOT / "automation/cost-report/send_cost_report.py")],
        env=env, text=True, capture_output=True, timeout=10, check=False,
    )
    # Then
    assert completed.returncode == (0 if valid else 1)
    if valid:
        assert completed.stdout == COST_LEGACY + "\n"
    else:
        assert completed.stdout.startswith("cost-report error:")
