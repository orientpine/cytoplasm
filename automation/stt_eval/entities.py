"""원문 문자 정렬 대각선과 참조 별칭에 근거한 발생 단위 개체 지표."""
from __future__ import annotations

from typing import TypedDict

from .model import EvalEntity, EvalRecord, EvalRecordError

KINDS = ("name", "number", "date")


class EntityStats(TypedDict):
    tp: int
    fp: int
    fn: int
    precision: float | None
    recall: float | None
    f1: float | None
    miss_rate: float | None


def entity_stats(tp: int, fp: int, fn: int) -> EntityStats:
    """분모 없는 비율은 성공 1이나 실패 0으로 꾸미지 않는다."""
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
            "miss_rate": fn / (tp + fn) if tp + fn else None}


def _validate(record: EvalRecord) -> None:
    # 공개 함수는 메모리 레코드도 받으므로 개체 입력 경계를 검증한다.
    for entity in record.entities:
        if entity.kind not in KINDS:
            raise EvalRecordError("entities.kind: 알 수 없는 종류입니다")
        if (type(entity.char_start) is not int or type(entity.char_end) is not int
                or not 0 <= entity.char_start <= entity.char_end <= len(record.text)):
            raise EvalRecordError("entities: 텍스트 범위 안의 단조 스팬이어야 합니다")


def _diagonals(ref: str, hyp: str) -> dict[int, int]:
    """스팬 좌표를 보존한다. 동률은 대각 > 삭제 > 삽입, 치환도 대각이다."""
    previous = list(range(len(hyp) + 1))
    trace = [bytearray([2] * (len(hyp) + 1))]
    for i, left in enumerate(ref, 1):
        row = [i] + [0] * len(hyp)
        steps = bytearray(len(hyp) + 1)
        steps[0] = 1
        for j, right in enumerate(hyp, 1):
            choices = (previous[j - 1] + (left != right), previous[j] + 1, row[j - 1] + 1)
            row[j] = min(choices)
            steps[j] = choices.index(row[j])
        trace.append(steps)
        previous = row
    aligned: dict[int, int] = {}
    i, j = len(ref), len(hyp)
    while i or j:
        step = trace[i][j]
        if step == 0:
            i, j = i - 1, j - 1
            aligned[i] = j
        elif step == 1:
            i -= 1
        else:
            j -= 1
    return aligned


def _corresponds(ref: EvalEntity, hyp: EvalEntity, aligned: dict[int, int]) -> bool:
    return any(hyp.char_start <= aligned.get(pos, -1) < hyp.char_end
               for pos in range(ref.char_start, ref.char_end))


def _match(edges: list[list[int]]) -> int:
    # 증가 경로를 찾아 폭넓은 별칭이 좁은 정답의 가설을 빼앗지 않게 한다.
    owners: dict[int, int] = {}

    def augment(index: int, seen: set[int]) -> bool:
        for target in edges[index]:
            if target in seen:
                continue
            seen.add(target)
            if target not in owners or augment(owners[target], seen):
                owners[target] = index
                return True
        return False

    return sum(augment(index, set()) for index in range(len(edges)))


def entity_metrics(ref: EvalRecord, hyp: EvalRecord) -> dict[str, EntityStats]:
    """같은 종류·대각 대응·canonical 또는 참조 accepted 일치의 최대 일대일 매칭."""
    _validate(ref)
    _validate(hyp)
    aligned = _diagonals(ref.text, hyp.text) if ref.entities and hyp.entities else {}
    result: dict[str, EntityStats] = {}
    for kind in KINDS:
        refs = [entity for entity in ref.entities if entity.kind == kind]
        hyps = [entity for entity in hyp.entities if entity.kind == kind]
        edges = [[index for index, candidate in enumerate(hyps)
                  if (candidate.canonical == entity.canonical or candidate.canonical in entity.accepted)
                  and _corresponds(entity, candidate, aligned)] for entity in refs]
        tp = _match(edges)
        result[kind] = entity_stats(tp, len(hyps) - tp, len(refs) - tp)
    return result
