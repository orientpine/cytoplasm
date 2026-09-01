"""주간 리포트는 ISO 주 1회만 발송된다 (repair 티켓 t_cda4eea8).

2026-08 실측: Hermes cron 스케줄은 `0 9 * * 1` 하나뿐이었는데도 소유자는
2026-08-18·08-19 연속으로 「주간 연구 동향」 DM 을 받았다. cron 실행 기록이 없는
두 번의 임시 실행(8/17 import 실패 수리 검증)이 각각 실제 DM 을 보냈기 때문이다.
`run()` 이 호출될 때마다 무조건 발송하므로, 스케줄이 옳아도 사람이 한 번 더 돌리면
그 주에 두 번째 리포트가 나간다. 이 파일은 그 재발을 고정한다.
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

from automation.research_trends import research_trends  # noqa: E402
from automation.research_trends.research_trends import OwnerDmDeliveryError  # noqa: E402


class _Runs:
    """무엇이 실제로 일어났는지만 센다 — 생성 횟수와 발송 횟수."""

    def __init__(self) -> None:
        self.generated: list[str] = []
        self.sent: list[str] = []
        self.ingested = 0


def _at(moment: datetime, monkeypatch: pytest.MonkeyPatch) -> None:
    """모듈이 읽는 시계를 고정한다 (`datetime.now(KST)` 한 곳)."""
    monkeypatch.setattr(
        research_trends,
        "datetime",
        types.SimpleNamespace(now=lambda _tz: moment),
        raising=True,
    )


def _stub_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Runs:
    runs = _Runs()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("RESEARCH_TRENDS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("RESEARCH_TRENDS_DRY_RUN", raising=False)
    monkeypatch.setattr(research_trends, "_safe_topics", lambda: ("SLAM",))
    monkeypatch.setattr(research_trends, "weekly_quality_section", lambda *_a, **_k: "")

    def _run_topics(*_args: object) -> tuple[object, ...]:
        runs.generated.append("run_topics")
        return ()

    monkeypatch.setattr(research_trends.core, "run_topics", _run_topics)
    monkeypatch.setattr(research_trends, "_write_report", lambda report, _day: tmp_path / "r.md")
    monkeypatch.setattr(research_trends, "_send_dm", lambda report: runs.sent.append(report))

    def _ingest() -> None:
        runs.ingested += 1

    monkeypatch.setattr(research_trends, "_ingest_report", _ingest)
    return runs


def test_second_invocation_in_the_same_iso_week_does_not_send_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: 월요일 정규 실행이 리포트를 보낸 뒤
    runs = _stub_pipeline(tmp_path, monkeypatch)
    _at(datetime.fromisoformat("2026-08-17T09:00:00+09:00"), monkeypatch)
    assert research_trends.run() == 0
    _ = capsys.readouterr()

    # When: 같은 주 화요일에 사람이 같은 워처를 한 번 더 돌리면
    _at(datetime.fromisoformat("2026-08-18T23:16:00+09:00"), monkeypatch)
    return_code = research_trends.run()

    # Then: 두 번째 발송도, 두 번째 생성(arXiv·LLM 소비)도 없다.
    assert return_code == 0
    assert len(runs.sent) == 1
    assert len(runs.generated) == 1
    assert runs.ingested == 1
    assert capsys.readouterr().out == ""


def test_next_iso_week_sends_the_report_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    runs = _stub_pipeline(tmp_path, monkeypatch)
    _at(datetime.fromisoformat("2026-08-18T23:16:00+09:00"), monkeypatch)
    assert research_trends.run() == 0

    # When: 다음 ISO 주의 정규 실행
    _at(datetime.fromisoformat("2026-08-24T09:00:00+09:00"), monkeypatch)
    return_code = research_trends.run()

    # Then
    assert return_code == 0
    assert len(runs.sent) == 2


def test_failed_delivery_leaves_the_week_open_for_a_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 규약 (f) — 상태 마킹은 성공 이후. DM 이 실패한 주는 소진되지 않는다.
    runs = _stub_pipeline(tmp_path, monkeypatch)

    def _fail(_report: str) -> None:
        raise OwnerDmDeliveryError("owner DM channel is missing")

    monkeypatch.setattr(research_trends, "_send_dm", _fail)
    _at(datetime.fromisoformat("2026-08-17T09:00:00+09:00"), monkeypatch)
    with pytest.raises(OwnerDmDeliveryError):
        _ = research_trends.run()

    # When: 같은 주에 재시도하면
    monkeypatch.setattr(research_trends, "_send_dm", lambda report: runs.sent.append(report))
    _at(datetime.fromisoformat("2026-08-17T22:49:00+09:00"), monkeypatch)
    return_code = research_trends.run()

    # Then: 그 주의 첫 성공 발송이 나간다.
    assert return_code == 0
    assert len(runs.sent) == 1


def test_dry_run_neither_sends_nor_consumes_the_week(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 검증용 dry-run 은 발송하지 않으므로 주를 소진해서도 안 된다.
    runs = _stub_pipeline(tmp_path, monkeypatch)
    monkeypatch.setenv("RESEARCH_TRENDS_DRY_RUN", "1")
    _at(datetime.fromisoformat("2026-08-17T08:00:00+09:00"), monkeypatch)
    assert research_trends.run() == 0
    assert not runs.sent

    # When: 같은 주의 정규 실행
    monkeypatch.delenv("RESEARCH_TRENDS_DRY_RUN")
    _at(datetime.fromisoformat("2026-08-17T09:00:00+09:00"), monkeypatch)
    return_code = research_trends.run()

    # Then
    assert return_code == 0
    assert len(runs.sent) == 1
