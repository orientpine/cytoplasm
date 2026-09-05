"""교정 로그 — 무엇이 무엇으로 바뀌었는지 남기는 자리.

2026-09-05 전에는 교정 횟수 정수 하나(Polished.substitutions)만 남았고, 그마저 plaud 경로에서는
버려졌다. 그래서 퍼지 교정이 낱말을 잘못 고쳐도 사후에 발견할 방법이 파이프라인에 없었고,
"문맥 교정이 더 필요한가"를 판단할 근거 목록도 생기지 않았다.

이 파일은 그 로그의 계약을 고정한다: 바뀐 **어절만** 남기고 문장·문맥은 남기지 않으며(레코드
동일성 단언이 그 가드다), 쓰기에 실패해도 전사를 멈추지 않는다.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "speechtotext"
sys.path.insert(0, str(SKILL / "scripts"))

import stt_blocks  # noqa: E402
import stt_gap  # noqa: E402
import stt_correction_log  # noqa: E402
import stt_polish  # noqa: E402
import stt_speaker_flow  # noqa: E402
import stt_terms  # noqa: E402

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
FUZZY = stt_terms.Correction(before="항정기술", after="한전기술", term="한전기술", kind="fuzzy")


def _log(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "logs" / "corrections.jsonl"
    monkeypatch.setenv("SPEECHTOTEXT_CORRECTION_LOG", str(path))
    return path


def _records(path: Path) -> list[dict[str, object]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


class _Transcription:
    """stt_speaker_flow.tidy 가 요구하는 만큼의 오리 타입."""

    def __init__(self, sentences) -> None:
        self._sentences = tuple(sentences)

    @property
    def text(self) -> str:
        return " ".join(sentence.text for sentence in self._sentences)

    @property
    def sentences(self):
        return self._sentences


def _said(text: str, start: int, speaker: str = "") -> stt_blocks.TimedSentence:
    return stt_blocks.TimedSentence(text=text, start_ms=start, end_ms=start + 1000, speaker=speaker)


def test_a_fuzzy_repair_says_what_it_changed() -> None:
    """카운트는 '몇 번'만 답한다 — 오탐을 찾으려면 '무엇을'이 있어야 한다."""
    _fixed, corrections = stt_terms.correct("항정기술하고 얘기했다", ("한전기술",))

    assert corrections == (FUZZY,)


def test_an_exact_pair_is_recorded_as_a_different_kind() -> None:
    """소유자가 짝지은 치환과 기계가 추측한 교정은 신뢰도가 다르다 — 로그에서 갈라야 한다."""
    _fixed, corrections = stt_polish.apply_glossary("영무 보고", (("영무", "업무"),))

    assert corrections == (
        stt_terms.Correction(before="영무", after="업무", term="업무", kind="exact"),
    )


def test_one_pass_can_report_both_kinds() -> None:
    glossary = stt_polish.parse_glossary("영무,업무\n열교환기\n")

    _fixed, corrections = stt_polish.apply_glossary("영무 보고 중 열기환기 점검", glossary)

    assert [(c.kind, c.before) for c in corrections] == [("exact", "영무"), ("fuzzy", "열기환기")]


def test_the_log_keeps_the_word_and_never_the_sentence(tmp_path, monkeypatch) -> None:
    """레코드 동일성 단언이 프라이버시 가드다 — 필드가 하나라도 늘면 여기서 걸린다."""
    path = _log(tmp_path, monkeypatch)

    written = stt_correction_log.record(
        (FUZZY,), label="회의", project="해양고신뢰성", stage="transcribe", now=NOW
    )

    assert written == 1
    assert _records(path) == [
        {
            "after": "한전기술",
            "at": "2026-09-05T12:00:00+00:00",
            "before": "항정기술",
            "kind": "fuzzy",
            "label": "회의",
            "project": "해양고신뢰성",
            "stage": "transcribe",
            "term": "한전기술",
        }
    ]


def test_nothing_to_record_leaves_no_file(tmp_path, monkeypatch) -> None:
    """고친 것이 없는 다듬기가 빈 파일을 만들면 로그가 잡음이 된다."""
    path = _log(tmp_path, monkeypatch)

    assert stt_correction_log.record((), label="회의", project="", stage="polish", now=NOW) == 0
    assert not path.exists()


def test_the_log_is_owner_only(tmp_path, monkeypatch) -> None:
    """전사본에서 뽑은 낱말이라 다른 계정이 읽을 자리가 아니다."""
    path = _log(tmp_path, monkeypatch)

    stt_correction_log.record((FUZZY,), label="회의", project="", stage="transcribe", now=NOW)

    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_a_log_that_cannot_be_written_does_not_stop_the_transcript(
    tmp_path, monkeypatch, capsys
) -> None:
    """로그는 관측 수단이지 파이프라인의 전제가 아니다."""
    blocked = tmp_path / "blocked"
    blocked.write_text("", encoding="utf-8")
    monkeypatch.setenv("SPEECHTOTEXT_CORRECTION_LOG", str(blocked / "logs" / "corrections.jsonl"))

    written = stt_correction_log.record(
        (FUZZY,), label="회의", project="", stage="transcribe", now=NOW
    )

    assert written == 0
    assert "CORRECTION-LOG-FAIL" in capsys.readouterr().err


def test_polish_carries_every_correction_and_the_count_still_agrees() -> None:
    """substitutions 는 이제 파생값이다 — 소비자(polish_summary·run_summary)는 그대로 둔다."""
    glossary = stt_polish.parse_glossary("열교환기\n한전기술\n")

    polished = stt_polish.polish_sentences(
        (_said("열기환기 점검입니다.", 0), _said("항정기술 담당입니다.", 1000)), glossary=glossary
    )

    assert [c.before for c in polished.corrections] == ["열기환기", "항정기술"]
    assert polished.substitutions == len(polished.corrections)


def test_the_naming_pass_does_not_record_the_same_repair_twice() -> None:
    """tidy() 는 다듬기를 두 번 부른다 — 두 번째 패스는 이름만 렌더한다."""
    transcription = _Transcription((_said("열기환기 점검입니다.", 0, "화자1"),))

    polished, _speakers = stt_speaker_flow.tidy(
        transcription, stt_polish.parse_glossary("열교환기\n")
    )

    assert [c.before for c in polished.corrections] == ["열기환기"]
    assert polished.substitutions == 1


def test_a_gap_marker_is_never_touched_or_recorded() -> None:
    """표식은 발화가 아니다 — 용어가 그 안의 낱말을 건드리면 어느 분이 비었는지 말하는 줄이
    조용히 바뀌고, 그 교정이 로그에 진짜 교정인 척 남는다.

    글로서리 "구간막" 은 표식 안의 "구간만" 과 자모 하나 차이라 가드가 없으면 실제로 걸린다.
    """
    marker = stt_gap.marker(105_000, 210_000)

    polished = stt_polish.polish_sentences(
        (_said(marker, 105_000),), glossary=stt_polish.parse_glossary("구간막\n")
    )

    assert polished.corrections == ()
    assert marker in polished.body
