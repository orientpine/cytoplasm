"""시트 실패로 적재된 재시도가 **다음 성공 틱에서 실제로 소비**되는가 — 회귀 고정 (CR-3).

2026-08-23 23:30 실측: Google Sheets 일시 503 으로 `budget_cli` 가 exit 4 를 내며
`SHEET-FAIL retry_queued id=... reason=...` 를 남겼다. 설계상 그 큐는 다음 30분 틱의
성공한 읽기가 해소한다(`budget_cli._snapshot` → `budget_store.resolve_retries`) —
"큐에 넣고 아무도 비우지 않는" 조용한 누수가 아니라는 것이 이 파일이 못박는 계약이다.

경계 주의: `cmd_watch` 는 `E2E_TEST_MODE` 를 거부하는 프로덕션 전용 틱이므로 여기서는
그 환경을 쓰지 않는다. 시트는 `BUDGET_SHEET_FILE` 픽스처로, 게이트/DB/설정은 tmp 로
가둔다. 자식 프로세스(gws) 호출은 아예 금지시켜 네트워크 부재를 증명한다.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "budget" / "scripts"))

import budget_cli  # noqa: E402
import budget_core  # noqa: E402
import budget_gate  # noqa: E402
import budget_store  # noqa: E402

OWNER_ID = "owner-1"
ROWS = [
    ["인건비", "100", "10", "90", "2026-08-23"],
    ["재료비", "200", "0", "200", "2026-08-23"],
]


def _payload(rows: list[list[str]]) -> str:
    values = [["[규칙]"], ["1"], ["2"], ["3"], [], list(budget_core.HEADER_EXPECTED), *rows]
    return json.dumps({"majorDimension": "ROWS", "values": values}, ensure_ascii=False)


@pytest.fixture
def budget_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """DB·게이트·설정을 tmp 로 가두고, 어떤 자식 프로세스도 못 뜨게 막는다."""
    config = tmp_path / "interop-config.json"
    _ = config.write_text(json.dumps({"owner_id": OWNER_ID}), encoding="utf-8")
    monkeypatch.setenv("BUDGET_DB", str(tmp_path / "budget.db"))
    monkeypatch.setenv("BUDGET_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("INTEROP_CONFIG", str(config))
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)

    def no_subprocess(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("이 경로는 gws/네트워크를 부르면 안 된다")

    monkeypatch.setattr(budget_cli.subprocess, "run", no_subprocess)
    return tmp_path


def _sheet_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """503 과 같은 모양의 읽기 실패 — `SheetAccessError` → retry 적재 경로."""
    monkeypatch.setenv("BUDGET_SHEET_FILE", str(tmp_path / "unreachable-sheet.json"))


def _sheet_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    sheet = tmp_path / "sheet.json"
    _ = sheet.write_text(_payload(ROWS), encoding="utf-8")
    monkeypatch.setenv("BUDGET_SHEET_FILE", str(sheet))
    return sheet


def _db(tmp_path: Path) -> Path:
    return tmp_path / "budget.db"


def _resolved_at(tmp_path: Path) -> list[tuple[int, str | None]]:
    with sqlite3.connect(_db(tmp_path)) as connection:
        rows = connection.execute("SELECT id, resolved_at FROM retry_queue ORDER BY id").fetchall()
    return [(int(row[0]), row[1]) for row in rows]


def _watch_tick() -> int:
    """프로덕션 cron 틱 그대로 — 대기 초안 0건이면 곧장 스냅샷 경로로 간다."""
    return budget_cli.cmd_watch(argparse.Namespace(no_post=True))


def test_a_sheet_failure_queues_a_retry_and_exits_four(
    budget_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the sheet read fails the way the 2026-08-23 503 did.
    _sheet_missing(budget_env, monkeypatch)

    # When: the snapshot path runs.
    code = budget_cli.cmd_snapshot(argparse.Namespace(no_post=True))

    # Then: 실패는 조용히 사라지지 않고 큐 행 하나로 남는다.
    captured = capsys.readouterr()
    assert code == 4
    pending = budget_store.pending_retries(_db(budget_env))
    assert len(pending) == 1
    assert f"SHEET-FAIL retry_queued id={pending[0][0]}" in captured.err


def test_the_next_successful_watch_tick_consumes_the_queued_retry(
    budget_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a retry queued by a failed tick.
    _sheet_missing(budget_env, monkeypatch)
    assert budget_cli.cmd_snapshot(argparse.Namespace(no_post=True)) == 4
    assert len(budget_store.pending_retries(_db(budget_env))) == 1
    _ = capsys.readouterr()

    # When: the next production tick reads the sheet successfully.
    _ = _sheet_ok(budget_env, monkeypatch)
    code = _watch_tick()

    # Then: 큐는 소비되고(해소 시각 기록) 스냅샷도 전진한다 — 누수 없음.
    captured = capsys.readouterr()
    assert code == 0
    assert "RETRY-RESOLVED n=1" in captured.out
    assert budget_store.pending_retries(_db(budget_env)) == []
    assert [resolved for _, resolved in _resolved_at(budget_env)] != [None]
    assert budget_store.latest_snapshot(_db(budget_env)) is not None


def test_a_healthy_tick_after_consumption_says_nothing_about_retries(
    budget_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a queued retry that a successful tick already consumed.
    _sheet_missing(budget_env, monkeypatch)
    assert budget_cli.cmd_snapshot(argparse.Namespace(no_post=True)) == 4
    _ = _sheet_ok(budget_env, monkeypatch)
    assert _watch_tick() == 0
    consumed = _resolved_at(budget_env)
    _ = capsys.readouterr()

    # When: the following healthy tick runs.
    code = _watch_tick()

    # Then: 같은 행을 두 번 소비하지 않고, 성공 틱이 해소를 가장하지도 않는다.
    captured = capsys.readouterr()
    assert code == 0
    assert "RETRY-RESOLVED" not in captured.out
    assert "NO-CHANGE" in captured.out
    assert _resolved_at(budget_env) == consumed


def test_a_failing_tick_leaves_earlier_rows_pending_and_one_success_clears_them_all(
    budget_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a stale unresolved row from an older incident, then two fresh failures.
    stale = budget_store.queue_retry(_db(budget_env), "지난 사고 잔여 행", "2026-08-01T00:00:00Z")
    _sheet_missing(budget_env, monkeypatch)
    for _ in range(2):
        assert budget_cli.cmd_snapshot(argparse.Namespace(no_post=True)) == 4
    pending_ids = [row[0] for row in budget_store.pending_retries(_db(budget_env))]
    assert pending_ids[0] == stale
    assert len(pending_ids) == 3
    _ = capsys.readouterr()

    # When: one tick finally succeeds.
    _ = _sheet_ok(budget_env, monkeypatch)
    code = _watch_tick()

    # Then: 실패 틱은 아무것도 해소하지 않았고, 성공 틱 하나가 대기 행 전체를 한 번에 비운다.
    captured = capsys.readouterr()
    assert code == 0
    assert "RETRY-RESOLVED n=3" in captured.out
    assert budget_store.pending_retries(_db(budget_env)) == []


def test_the_operator_queue_report_tracks_the_same_rows(
    budget_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: one queued retry.
    _sheet_missing(budget_env, monkeypatch)
    assert budget_cli.cmd_snapshot(argparse.Namespace(no_post=True)) == 4
    _ = capsys.readouterr()

    # When / Then: the `retry-queue` surface reports it, and stops reporting it once consumed.
    assert budget_cli.cmd_retry_queue(argparse.Namespace()) == 0
    queued_report = capsys.readouterr().out
    assert "RETRY-PENDING id=" in queued_report
    assert "RETRY-QUEUE pending=1" in queued_report

    _ = _sheet_ok(budget_env, monkeypatch)
    assert _watch_tick() == 0
    _ = capsys.readouterr()
    assert budget_cli.cmd_retry_queue(argparse.Namespace()) == 0
    assert "RETRY-QUEUE pending=0" in capsys.readouterr().out


def test_the_consuming_tick_is_the_production_watch_path_with_no_drafts(
    budget_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the gate holds no drafts, so the tick's only work is the snapshot path.
    _sheet_missing(budget_env, monkeypatch)
    assert budget_cli.cmd_snapshot(argparse.Namespace(no_post=True)) == 4
    _ = _sheet_ok(budget_env, monkeypatch)
    _ = capsys.readouterr()

    # When: the cron tick runs end to end.
    code = _watch_tick()

    # Then: 승인 경로는 건드리지 않은 채 큐만 소비된다 — 소비가 스냅샷 경로에 있다는 증거.
    captured = capsys.readouterr()
    assert code == 0
    assert budget_gate.list_drafts() == []
    assert "RETRY-RESOLVED n=1" in captured.out
    assert "DRAFT-CREATED" not in captured.out
