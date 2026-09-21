"""주간 연구 동향은 #notifications 에 메시지 한 건(요약 + 전문 첨부)으로 간다.

2026-09-21 실측: 13.4KB 보고가 2000자 청크 7건으로 잘려 갔고, 그 채널을 수신하던 peer
게이트웨이가 청크마다 스레드를 열어 논평을 달았다. 청크는 문장 중간에서 끊겨
(「교하기는 어렵다. 논문 링크: …」) 채널에서 읽을 수 없었다. 이 파일은 발송 형태를
고정한다 — 요약 head 는 한 메시지 안에 들어가고, 전문은 이미 디스크에 있는 보고 파일을
첨부로 싣는다. `test_research_trends_weekly_delivery.py` 는 주 1회 상한만 다루므로 여기에
갈라 둔다.
"""

from __future__ import annotations

import os
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("TOPICS_SCRIPTS", str(_ROOT / "skills" / "topics" / "scripts"))
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "automation" / "research_trends"))

from automation import owner_notice  # noqa: E402
from automation.research_trends import research_trends  # noqa: E402
from automation.research_trends import research_trends_core as core  # noqa: E402

_PAPER = core.Paper("T", "abstract", "https://arxiv.org/abs/2609.00001", "2026-09-16", "arXiv")


def _long_report() -> str:
    return "📚 주간 연구 동향 — 2026-09-21 KST\n" + ("## topic\n" + "내용 " * 400 + "\n") * 6


def test_report_summary_names_topics_counts_and_the_attached_file() -> None:
    # Given: three outcomes — papers, a source failure, an empty search.
    outcomes = (
        core.TopicOutcome("autonomous excavator", (_PAPER, _PAPER, _PAPER), "요약", None),
        core.TopicOutcome("SLAM", (), "", "출처 조회 실패"),
        core.TopicOutcome("agri robot", (), "이번 주 검색 결과가 없습니다.", None),
    )
    # When
    summary = core.report_summary("2026-09-21", outcomes, "research-trends-20260921.md")
    # Then: one screen — heading, totals, one line per topic, and where the full text is.
    assert summary.startswith("📚 주간 연구 동향 — 2026-09-21 KST")
    assert "주제 3개 · 논문 3편" in summary
    assert "- autonomous excavator: 논문 3편" in summary
    assert "- SLAM: ⚠️ 출처 조회 실패" in summary
    assert "- agri robot: 검색 결과 없음" in summary
    assert "research-trends-20260921.md" in summary
    assert len(summary) < 1900


def test_send_dm_sends_the_summary_and_attaches_the_report_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the report already lives on disk (it is what gets ingested into RAG).
    report = _long_report()
    report_path = tmp_path / "research-trends-20260921.md"
    _ = report_path.write_text(report + "\n", encoding="utf-8")
    calls: list[tuple[str, object, tuple[Path, ...]]] = []

    def _notify(body: str, *, message: object = None, attachments: tuple[Path, ...] = ()) -> bool:
        calls.append((body, message, tuple(attachments)))
        return True

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fixture-token")
    monkeypatch.setattr(owner_notice, "notify_owner", _notify)
    summary = "📚 주간 연구 동향 — 2026-09-21 KST\n주제 6개 · 논문 0편 — 전문은 첨부"

    # When
    research_trends._send_dm(report, report_path=report_path, summary=summary)

    # Then: one facade call, the short head as the body, the file as the attachment,
    # and the envelope fact carries the head — not the 13KB report.
    assert len(calls) == 1
    body, message, attachments = calls[0]
    assert body == summary
    assert attachments == (report_path,)
    assert message is not None
    assert getattr(message, "fact").startswith(summary)
    assert "내용 내용" not in getattr(message, "fact")
    assert getattr(message, "render_version") == "owner-ko-v2"  # v1 flattens the topic lines


def test_send_dm_without_a_report_file_still_sends_the_full_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: no file to attach (legacy call shape) — nothing to lose, so send the text.
    calls: list[tuple[str, tuple[Path, ...]]] = []

    def _notify(body: str, *, message: object = None, attachments: tuple[Path, ...] = ()) -> bool:
        calls.append((body, tuple(attachments)))
        return True

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fixture-token")
    monkeypatch.setattr(owner_notice, "notify_owner", _notify)
    # When
    research_trends._send_dm("report", summary="요약")
    # Then
    assert calls == [("report", ())]


def test_run_hands_the_summary_and_the_report_path_to_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the pipeline produced one topic with one paper.
    seen: list[dict[str, object]] = []
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("RESEARCH_TRENDS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("RESEARCH_TRENDS_REPORT_DIR", str(tmp_path / "notes"))
    monkeypatch.delenv("RESEARCH_TRENDS_DRY_RUN", raising=False)
    monkeypatch.setattr(research_trends, "_safe_topics", lambda: ("SLAM",))
    monkeypatch.setattr(research_trends, "weekly_quality_section", lambda *_a, **_k: "")
    monkeypatch.setattr(
        research_trends.core, "run_topics",
        lambda *_a: (core.TopicOutcome("SLAM", (_PAPER,), "요약", None),),
    )
    monkeypatch.setattr(research_trends, "_ingest_report", lambda: None)
    monkeypatch.setattr(
        research_trends, "datetime",
        types.SimpleNamespace(now=lambda _tz: datetime.fromisoformat("2026-09-21T09:00:00+09:00")),
    )
    monkeypatch.setattr(
        research_trends, "_send_dm",
        lambda report, **kwargs: seen.append({"report": report, **kwargs}),
    )
    # When
    assert research_trends.run() == 0
    # Then: delivery receives the file it should attach and the head it should show.
    assert len(seen) == 1
    assert isinstance(seen[0]["report_path"], Path)
    assert seen[0]["report_path"].name == "research-trends-20260921.md"
    summary = seen[0]["summary"]
    assert isinstance(summary, str)
    assert "- SLAM: 논문 1편" in summary
    assert "research-trends-20260921.md" in summary
    assert "## SLAM" in str(seen[0]["report"])
