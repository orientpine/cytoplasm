"""과제별×년도별 다중 시트 처리 계약 (registry 모드) + 레거시 모드 불변 회귀.

기존 단일 시트 계약은 test_budget_skill.py가 무수정으로 지키고, 이 파일은
registry 모드에서만 열리는 동작(시트별 스냅샷 스트림·claim/승인 키 스코프·
부분 실패 격리)과 레거시 포맷 불변(claim key·출력 라벨 부재)을 고정한다.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "budget" / "scripts"))

import budget_approval  # noqa: E402
import budget_cli  # noqa: E402
import budget_core  # noqa: E402
import budget_gate  # noqa: E402
import budget_store  # noqa: E402

ROWS_A = [("인건비", "100", "10", "90", "2026-08-01")]
ROWS_B = [("재료비", "200", "20", "180", "2026-08-01")]


def _grid(rows: list[tuple[str, ...]]) -> list[list[str]]:
    return [[], [], [], [], [], list(budget_core.HEADER_EXPECTED)] + [list(r) for r in rows]


def _registry_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = {
        "version": 1,
        "projects": {
            "autophagy": {"2026": "sheet-au-2026"},
            "무인굴착기": {"2026": "sheet-ex-2026"},
        },
    }
    path = tmp_path / "sheets.json"
    path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("BUDGET_SHEETS_FILE", str(path))
    monkeypatch.delenv("BUDGET_SHEET_ID", raising=False)
    monkeypatch.setenv("BUDGET_DB", str(tmp_path / "budget.db"))
    monkeypatch.setenv("BUDGET_GATE_DIR", str(tmp_path / "gate"))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"mail_to": "admin@example.com"}), encoding="utf-8")
    monkeypatch.setenv("BUDGET_CONFIG", str(config))


def _legacy_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUDGET_SHEETS_FILE", str(tmp_path / "absent.json"))
    monkeypatch.setenv("BUDGET_SHEET_ID", "legacy-sheet")
    monkeypatch.setenv("BUDGET_DB", str(tmp_path / "budget.db"))
    monkeypatch.setenv("BUDGET_GATE_DIR", str(tmp_path / "gate"))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"mail_to": "admin@example.com"}), encoding="utf-8")
    monkeypatch.setenv("BUDGET_CONFIG", str(config))


def _snapshot_args() -> argparse.Namespace:
    return argparse.Namespace(no_post=True, origin_channel_id="", origin_message_id="")


def _fake_reader(payloads: dict[str, list[list[str]]]):
    def read(sheet_id: str) -> list[list[str]]:
        grid = payloads.get(sheet_id)
        if grid is None:
            raise budget_cli.SheetAccessError(f"gws sheets read 실패 rc=1: {sheet_id}")
        return grid

    return read


def test_store_snapshot_streams_isolated_per_sheet_key(tmp_path: Path) -> None:
    db = tmp_path / "budget.db"
    budget_store.store_snapshot(db, "h-a", ROWS_A, "t1", sheet_key="autophagy/2026")
    budget_store.store_snapshot(db, "h-b", ROWS_B, "t2", sheet_key="무인굴착기/2026")
    latest_a = budget_store.latest_snapshot(db, sheet_key="autophagy/2026")
    latest_b = budget_store.latest_snapshot(db, sheet_key="무인굴착기/2026")
    assert latest_a is not None and latest_a[0] == "h-a"
    assert latest_b is not None and latest_b[0] == "h-b"
    assert budget_store.latest_snapshot(db) is None


def test_store_upgrades_pre_sheet_key_db_rows_onto_legacy_stream(tmp_path: Path) -> None:
    db = tmp_path / "budget.db"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE snapshots (taken_at TEXT NOT NULL, hash TEXT NOT NULL,"
            " rows_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO snapshots (taken_at, hash, rows_json) VALUES (?, ?, ?)",
            ("t0", "old-hash", json.dumps([list(ROWS_A[0])])),
        )
    latest = budget_store.latest_snapshot(db)
    assert latest is not None and latest[0] == "old-hash"
    budget_store.store_snapshot(db, "h-a", ROWS_A, "t1", sheet_key="autophagy/2026")
    scoped = budget_store.latest_snapshot(db, sheet_key="autophagy/2026")
    assert scoped is not None and scoped[0] == "h-a"
    legacy = budget_store.latest_snapshot(db)
    assert legacy is not None and legacy[0] == "old-hash"


def test_claim_key_legacy_format_is_unchanged() -> None:
    prev, new = "a" * 64, "b" * 64
    assert budget_core.claim_key(prev, new) == f"{prev[:16]}->{new[:16]}"


def test_claim_key_prefixes_sheet_key_when_present() -> None:
    prev, new = "a" * 64, "b" * 64
    scoped = budget_core.claim_key(prev, new, sheet_key="autophagy/2026")
    assert scoped == f"autophagy/2026:{prev[:16]}->{new[:16]}"


def test_render_mail_context_reaches_subject_and_body() -> None:
    from datetime import UTC, datetime

    changes = [budget_core.Change("인건비", "잔액", "90", "80")]
    now = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)
    subject, body = budget_core.render_mail(
        changes, prev_hash="p" * 64, new_hash="n" * 64, now=now, context="autophagy/2026"
    )
    assert "autophagy/2026" in subject
    assert "autophagy/2026" in body
    legacy_subject, _ = budget_core.render_mail(
        changes, prev_hash="p" * 64, new_hash="n" * 64, now=now
    )
    assert legacy_subject == "[과제비] 원장 변경 통지 및 처리 요청 (2026-08-24)"


def test_approvals_message_names_project_when_present() -> None:
    draft = {
        "changes": [["인건비", "잔액", "90", "80"]],
        "prev_hash": "p" * 64,
        "new_hash": "n" * 64,
        "id": "abc123",
        "sha256": "s" * 64,
        "project": "무인굴착기",
        "year": 2026,
    }
    message = budget_core.render_approvals_message(draft)
    assert "무인굴착기" in message and "2026" in message
    legacy = budget_core.render_approvals_message({**draft, "project": "", "year": 0})
    assert "과제:" not in legacy


def test_create_draft_persists_project_outside_the_sha_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BUDGET_GATE_DIR", str(tmp_path / "gate"))
    changes = [budget_core.Change("인건비", "잔액", "90", "80")]
    draft = budget_gate.create_draft(
        changes=changes, subject="s", body="b", recipient="admin@example.com",
        prev_hash="p" * 64, new_hash="n" * 64, claim_key="autophagy/2026:x->y",
        project="autophagy", year=2026,
    )
    assert (draft["project"], draft["year"]) == ("autophagy", 2026)
    stripped = {k: v for k, v in draft.items() if k not in {"project", "year", "sha256"}}
    assert budget_core.draft_sha256(stripped) == draft["sha256"]


def test_approval_key_scopes_by_project_and_year() -> None:
    scoped = budget_approval.approval_key(
        {"mail_to": "admin@example.com", "project": "autophagy", "year": 2026}
    )
    assert scoped == "budget:admin@example.com:autophagy/2026"
    legacy = budget_approval.approval_key({"mail_to": "admin@example.com"})
    assert legacy == "budget:admin@example.com"


def test_query_parser_accepts_project_and_year() -> None:
    args = budget_cli.build_parser().parse_args(
        ["query", "--project", "autophagy", "--year", "2026"]
    )
    assert (args.project, args.year) == ("autophagy", 2026)


def test_query_selects_the_registered_sheet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_env(tmp_path, monkeypatch)
    seen: list[str] = []

    def read(sheet_id: str) -> list[list[str]]:
        seen.append(sheet_id)
        return _grid(ROWS_A)

    monkeypatch.setattr(budget_cli, "read_balance_values", read)
    rc = budget_cli.cmd_query(
        argparse.Namespace(item="", project="autophagy", year=0)
    )
    assert rc == 0 and seen == ["sheet-au-2026"]
    assert "BUDGET-OK" in capsys.readouterr().out


def test_query_multiple_projects_without_flag_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry_env(tmp_path, monkeypatch)
    with pytest.raises(budget_gate.GateError) as caught:
        budget_cli.cmd_query(argparse.Namespace(item="", project="", year=0))
    assert caught.value.exit_code == 2


def test_query_flags_without_registry_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _legacy_env(tmp_path, monkeypatch)
    with pytest.raises(budget_gate.GateError) as caught:
        budget_cli.cmd_query(argparse.Namespace(item="", project="autophagy", year=0))
    assert caught.value.exit_code == 3


def test_snapshot_keeps_isolated_streams_and_scopes_the_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_env(tmp_path, monkeypatch)
    payloads = {"sheet-au-2026": _grid(ROWS_A), "sheet-ex-2026": _grid(ROWS_B)}
    monkeypatch.setattr(budget_cli, "read_balance_values", _fake_reader(payloads))

    assert budget_cli.cmd_snapshot(_snapshot_args()) == 0
    first = capsys.readouterr().out
    assert first.count("BASELINE") == 2
    assert "sheet=autophagy/2026" in first and "sheet=무인굴착기/2026" in first

    assert budget_cli.cmd_snapshot(_snapshot_args()) == 0
    assert capsys.readouterr().out.count("NO-CHANGE") == 2

    payloads["sheet-au-2026"] = _grid([("인건비", "100", "30", "70", "2026-08-24")])
    assert budget_cli.cmd_snapshot(_snapshot_args()) == 0
    third = capsys.readouterr().out
    assert third.count("DRAFT-CREATED") == 1 and third.count("NO-CHANGE") == 1

    drafts = list((tmp_path / "gate" / "drafts").glob("*.json"))
    assert len(drafts) == 1
    record = json.loads(drafts[0].read_text(encoding="utf-8"))
    assert record["project"] == "autophagy" and record["year"] == 2026
    assert record["claim_key"].startswith("autophagy/2026:")
    assert "autophagy/2026" in record["subject"]


def test_snapshot_partial_failure_isolates_and_exits_4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_env(tmp_path, monkeypatch)
    payloads = {"sheet-au-2026": _grid(ROWS_A)}
    monkeypatch.setattr(budget_cli, "read_balance_values", _fake_reader(payloads))
    rc = budget_cli.cmd_snapshot(_snapshot_args())
    assert rc == 4
    captured = capsys.readouterr()
    assert "BASELINE" in captured.out and "sheet=autophagy/2026" in captured.out
    assert "SHEET-FAIL" in captured.err and "무인굴착기/2026" in captured.err
    pending = budget_store.pending_retries(tmp_path / "budget.db")
    assert len(pending) == 1 and pending[0][1].startswith("[무인굴착기/2026]")


def test_snapshot_legacy_mode_output_carries_no_sheet_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _legacy_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        budget_cli, "read_balance_values", _fake_reader({"legacy-sheet": _grid(ROWS_A)})
    )
    assert budget_cli.cmd_snapshot(_snapshot_args()) == 0
    out = capsys.readouterr().out
    assert "BASELINE" in out and "sheet=" not in out


def test_sheets_command_masks_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_env(tmp_path, monkeypatch)
    rc = budget_cli.cmd_sheets(argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 0
    assert "autophagy" in out and "무인굴착기" in out
    assert "sheet-au-2026" not in out and "sheet-ex-2026" not in out
    assert "SHEETS-OK n=2" in out
