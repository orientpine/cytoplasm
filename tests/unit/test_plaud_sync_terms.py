"""``automation.plaud_sync.terms`` — 라이프로그 노트 교정의 효과 경계.

교정 판정은 공용 엔진이 하고 렌더 직전 적용은 note.py 가 한다. 여기서 보는 것은 둘뿐이다:
그 노드가 쓸 참고 문서를 어디서 읽는가, 그리고 무엇이 바뀌었는지를 어디에 적는가. 둘 다
실패해도 노트는 나가야 한다 — 교정은 노트의 전제가 아니라 품질이다.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from automation import term_correction, term_correction_log, term_glossary
from automation.plaud_sync import terms

_CORRECTION = term_correction.Correction(
    before="항정기술", after="한전기술", term="한전기술", kind=term_correction.FUZZY
)


def test_glossary_reads_the_explicit_file_when_one_is_named(tmp_path: Path) -> None:
    path = tmp_path / "용어집.csv"
    path.write_text("한전기술\n열기환기,열교환기\n", encoding="utf-8")

    assert terms.glossary({"TERM_GLOSSARY_FILE": str(path)}) == (
        ("한전기술", "한전기술"),
        ("열기환기", "열교환기"),
    )


def test_glossary_falls_back_to_the_node_cache_because_plaud_never_publishes_to_drive(
    tmp_path: Path,
) -> None:
    # plaud 는 DRIVE_PUBLISH_ENABLED=0 으로 돈다 — 캐시로 내려가는 것이 우회가 아니라 정상 경로다.
    cache = tmp_path / "term-glossary"
    cache.mkdir()
    (cache / "lifelog.csv").write_text("한전기술\n", encoding="utf-8")

    assert terms.glossary({"TERM_GLOSSARY_CACHE": str(cache)}) == (("한전기술", "한전기술"),)


def test_glossary_when_the_lookup_raises_then_the_note_still_gets_its_body(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> tuple[tuple[str, str], ...]:
        raise RuntimeError("drive down")

    monkeypatch.setattr(term_glossary, "glossary_for", _boom)

    assert terms.glossary({}) == ()
    assert "GLOSSARY-FETCH-FAIL kind=lifelog" in capsys.readouterr().err


def test_record_writes_the_document_kind_and_the_note_stage(tmp_path: Path) -> None:
    log = tmp_path / "corrections.jsonl"

    assert terms.record((_CORRECTION,), label="산책", env={"TERM_CORRECTION_LOG": str(log)}) == 1

    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert (entry["document"], entry["stage"], entry["label"]) == ("lifelog", "note", "산책")
    assert (entry["before"], entry["after"], entry["kind"]) == ("항정기술", "한전기술", "fuzzy")
    assert entry["project"] == ""
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_record_when_the_log_fails_then_it_is_a_marker_and_not_an_exception(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("disk gone")

    monkeypatch.setattr(term_correction_log, "record", _boom)

    assert terms.record((_CORRECTION,), label="산책") == 0
    assert term_correction_log.MARKER in capsys.readouterr().err
