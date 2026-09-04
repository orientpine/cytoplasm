"""``skills/plaud`` — read-only status of the plaud lifelog sync watcher.

The owner asked for a skill so "read ~/.hermes/plaud-sync/state.json and tell me the
per-status counts and last_poll_at" does not have to be remembered as a path. The CLI
is stdlib-only so the sandbox (disposable HOME, no repo on sys.path) can run it, and it
never writes anywhere.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

_REPO: Final = Path(__file__).resolve().parents[2]
_CLI: Final = _REPO / "skills" / "plaud" / "scripts" / "plaud_cli.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("plaud_cli_under_test", _CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _record(status: str, **extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "recording_id": "rec-x",
        "recorded_at": "2026-09-02T04:00:00Z",
        "note_relpath": "000_PARA/Area/Lifelog/2026/2026-09-02-x--abcdef123456.md",
        "status": status,
        "channel_id": "111",
        "message_id": "m-1" if status == "posted" else None,
        "approval_thread_id": "111" if status == "posted" else None,
    }
    base.update(extra)
    return base


def _state(**records: dict[str, object]) -> dict[str, object]:
    return {"version": 1, "last_poll_at": "2026-09-02T08:38:52Z", "records": records}


def test_summarize_counts_every_status_and_lists_cards_waiting_for_the_owner() -> None:
    cli = _load()
    summary = cli.summarize(
        _state(
            a=_record("posted", recording_id="rec-a"),
            b=_record("planned", recording_id="rec-b"),
            c=_record("written", recording_id="rec-c"),
            d=_record("posted", recording_id="rec-d", approval_thread_id="222"),
        )
    )
    assert summary.total == 4
    assert dict(summary.counts) == {
        "transcribing": 0, "planned": 1, "posted": 2, "approved": 0, "written": 1,
        "abandoned": 0,
    }
    assert summary.last_poll_at == "2026-09-02T08:38:52Z"
    assert [(p.recording_id, p.thread_id) for p in summary.pending] == [
        ("rec-a", "111"), ("rec-d", "222"),
    ]
    assert summary.pending[0].note_name == "2026-09-02-x--abcdef123456.md"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"version": 1, "records": []},
        _state(a=_record("teleported")),
        {"version": 1, "last_poll_at": 5, "records": {}},
    ],
)
def test_summarize_refuses_a_state_it_does_not_understand(payload: object) -> None:
    cli = _load()
    with pytest.raises(cli.StatusError):
        _ = cli.summarize(payload)


def test_status_reports_absent_state_without_failing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load()
    assert cli.main(["status", "--state", str(tmp_path / "missing.json")]) == 0
    assert capsys.readouterr().out.strip() == "PLAUD-STATUS state=absent"


def test_status_renders_counts_poll_time_in_kst_and_pending_cards(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load()
    path = tmp_path / "state.json"
    _ = path.write_text(
        json.dumps(_state(a=_record("posted", recording_id="rec-a"), b=_record("planned"))),
        encoding="utf-8",
    )
    assert cli.main(["status", "--state", str(path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("PLAUD-STATUS state=present")
    assert "2026-09-02T08:38:52Z" in out and "2026-09-02 17:38 KST" in out
    assert "레코드 2건" in out and "posted 1" in out and "planned 1" in out
    assert "rec-a · 스레드 111 · 2026-09-02-x--abcdef123456.md" in out


def test_status_json_is_machine_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load()
    path = tmp_path / "state.json"
    _ = path.write_text(json.dumps(_state(a=_record("posted"))), encoding="utf-8")
    assert cli.main(["status", "--state", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "present"
    assert payload["counts"]["posted"] == 1
    assert payload["pending"][0]["recording_id"] == "rec-x"


def test_status_exits_two_on_an_unreadable_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load()
    path = tmp_path / "state.json"
    _ = path.write_text("{", encoding="utf-8")
    assert cli.main(["status", "--state", str(path)]) == 2
    assert "PLAUD-STATUS state=unreadable" in capsys.readouterr().err


def test_status_lists_approved_records_with_their_write_failure_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load()
    path = tmp_path / "state.json"
    _ = path.write_text(
        json.dumps(
            _state(
                a=_record(
                    "approved",
                    recording_id="rec-a",
                    last_block_reason="write: ObsidianWriteError: fetch before upsert failed",
                ),
            )
        ),
        encoding="utf-8",
    )
    assert cli.main(["status", "--state", str(path)]) == 0
    out = capsys.readouterr().out
    assert "저장 대기(approved) 1건" in out
    assert "rec-a · 사유 write: ObsidianWriteError: fetch before upsert failed" in out
