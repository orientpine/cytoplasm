"""참조 단어의 양 끝 시각이 200ms 이내인 비율(T@200)을 계산한다."""
from __future__ import annotations

from fractions import Fraction

from .entities import _diagonals, _match
from .model import EvalRecord


def t200_counts(ref: EvalRecord, hyp: EvalRecord) -> tuple[int, int]:
    """시각 있는 참조 단어당 한 표이며 삭제·가설 시각 결측은 실패다.

    개체 지표의 원문 문자 대각 대응(치환 포함)과 최대 일대일 매칭을 재사용한다.
    양 끝 시각 모두 정수 ms 절대차 <=200이어야 성공하며 글자 수로 가중하지 않는다.
    참조 시각 결측은 분모에서 제외하고 단어/시각을 새로 보간하지 않는다.
    """
    refs = [word for word in ref.words if word.start_ms is not None and word.end_ms is not None]
    if not refs:
        return 0, 0
    aligned = _diagonals(ref.text, hyp.text) if hyp.words else {}
    edges = [[index for index, candidate in enumerate(hyp.words)
              if word.start_ms is not None and word.end_ms is not None
              and candidate.start_ms is not None and candidate.end_ms is not None
              and abs(word.start_ms - candidate.start_ms) <= 200
              and abs(word.end_ms - candidate.end_ms) <= 200
              and any(candidate.char_start <= aligned.get(pos, -1) < candidate.char_end
                      for pos in range(word.char_start, word.char_end))] for word in refs]
    return _match(edges), len(refs)


def t200(ref: EvalRecord, hyp: EvalRecord) -> Fraction | None:
    """참조 시각이 없으면 0이 아니라 판정 불가(None)를 반환한다."""
    matched, total = t200_counts(ref, hyp)
    return Fraction(matched, total) if total else None
