"""교정 감사 로그 — 어느 문서에서 무엇이 무엇으로 바뀌었는지, 노드에만."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from automation import term_correction, term_correction_log

_ONE = (
    term_correction.Correction(
        before="항정기술", after="한전기술", term="한전기술", kind=term_correction.FUZZY
    ),
)


def test_one_line_per_correction_names_the_document_that_was_corrected(tmp_path: Path) -> None:
    log = tmp_path / "corrections.jsonl"

    written = term_correction_log.record(
        _ONE,
        document="meeting",
        label="킥오프",
        project="해양고신뢰성",
        stage="minutes",
        env={term_correction_log.LOG_ENV: str(log)},
        now=datetime(2026, 9, 5, 4, 0, tzinfo=UTC),
    )

    assert written == 1
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row == {
        "after": "한전기술",
        "at": "2026-09-05T04:00:00+00:00",
        "before": "항정기술",
        "document": "meeting",
        "kind": "fuzzy",
        "label": "킥오프",
        "project": "해양고신뢰성",
        "stage": "minutes",
        "term": "한전기술",
    }


def test_the_log_never_carries_a_sentence(tmp_path: Path) -> None:
    """어절만 담는다 — 문맥을 함께 남기면 문서 본문이 로그로 새어 나간다."""
    log = tmp_path / "corrections.jsonl"
    term_correction_log.record(
        _ONE, document="lifelog", label="l", project="", stage="note",
        env={term_correction_log.LOG_ENV: str(log)},
    )

    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert max(len(str(value)) for key, value in row.items() if key != "at") <= 20


def test_nothing_corrected_writes_no_file(tmp_path: Path) -> None:
    log = tmp_path / "corrections.jsonl"

    assert term_correction_log.record(
        (), document="meeting", label="l", project="", stage="minutes",
        env={term_correction_log.LOG_ENV: str(log)},
    ) == 0
    assert not log.exists()


def test_a_write_that_fails_is_a_marker_and_not_an_exception(tmp_path: Path, capsys) -> None:
    """로그는 관측 수단이지 파이프라인의 전제가 아니다."""
    blocked = tmp_path / "taken"
    blocked.mkdir()

    written = term_correction_log.record(
        _ONE, document="meeting", label="l", project="", stage="minutes",
        env={term_correction_log.LOG_ENV: str(blocked)},
    )

    assert written == 0
    assert term_correction_log.MARKER in capsys.readouterr().err


def test_the_permissions_stay_locked(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "corrections.jsonl"
    term_correction_log.record(
        _ONE, document="meeting", label="l", project="", stage="minutes",
        env={term_correction_log.LOG_ENV: str(log)},
    )

    assert oct(log.stat().st_mode)[-3:] == "600"
    assert oct(log.parent.stat().st_mode)[-3:] == "700"


def test_the_default_path_lives_outside_any_checkout() -> None:
    path = term_correction_log.log_path({})

    assert path == Path(term_correction_log.DEFAULT_LOG).expanduser()
    assert ".hermes" in str(path)
    assert str(path).startswith(os.path.expanduser("~"))
