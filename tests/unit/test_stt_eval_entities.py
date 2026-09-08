"""개체는 값뿐 아니라 정렬된 발생 위치와 일대일 대응으로 센다."""
from collections.abc import Sequence
from dataclasses import replace
from importlib.util import find_spec
from typing import cast

import pytest

from automation.stt_eval.model import EntityKind, EvalEntity, EvalRecord, EvalRecordError


def api():
    assert find_spec("automation.stt_eval.entities") is not None, "entity metrics not implemented"
    from automation.stt_eval.entities import entity_metrics
    return entity_metrics


def record(text: str, entities: Sequence[EvalEntity]) -> EvalRecord:
    return EvalRecord("private-id", "a" * 64, 1000, text, entities=tuple(entities))


def entity(start: int, end: int, value: str, kind: str = "name", accepted: tuple[str, ...] = ()) -> EvalEntity:
    # 알 수 없는 kind를 주입하는 음성 테스트도 같은 생성 경계를 쓴다.
    return EvalEntity("private-entity", cast(EntityKind, kind), start, end, value, accepted)


def test_wrong_value_is_one_fp_and_one_fn():
    ref = record("김철수", [entity(0, 3, "김철수")])
    hyp = record("김영수", [entity(0, 3, "김영수")])
    result = api()(ref, hyp)["name"]
    assert (result["tp"], result["fp"], result["fn"]) == (0, 1, 1)
    assert result["precision"] == result["recall"] == result["f1"] == 0
    assert result["miss_rate"] == 1


def test_alias_and_shifted_character_alignment():
    ref = record("김철수 왔다", [entity(0, 3, "김철수", accepted=("철수",))])
    hyp = record("어 철수 왔다", [entity(2, 4, "철수")])
    result = api()(ref, hyp)["name"]
    assert (result["tp"], result["fp"], result["fn"]) == (1, 0, 0)
    assert result["f1"] == 1


def test_repeated_values_are_not_set_matches():
    ref = record("철수 철수", [entity(0, 2, "철수"), entity(3, 5, "철수")])
    hyp = record("철수 철수", [entity(3, 5, "철수")])
    result = api()(ref, hyp)["name"]
    assert (result["tp"], result["fp"], result["fn"]) == (1, 0, 1)
    wrong_location = replace(hyp, entities=(entity(3, 5, "철수"),))
    first_only = replace(ref, entities=(entity(0, 2, "철수"),))
    assert api()(first_only, wrong_location)["name"]["tp"] == 0


def test_matching_finds_maximum_not_greedy_and_never_reuses():
    ref = record("ab", [entity(0, 2, "a", accepted=("b",)), entity(0, 2, "a")])
    hyp = record("ab", [entity(0, 2, "a"), entity(0, 2, "b")])
    assert api()(ref, hyp)["name"]["tp"] == 2
    assert api()(replace(ref, entities=ref.entities[:1]), hyp)["name"]["fp"] == 1


def test_kind_mismatch_and_empty_denominators():
    ref = record("12", [entity(0, 2, "12", "number")])
    hyp = record("12", [entity(0, 2, "12", "date")])
    result = api()(ref, hyp)
    assert result["number"]["fn"] == result["date"]["fp"] == 1
    assert result["name"]["precision"] is None
    assert result["name"]["miss_rate"] is None


@pytest.mark.parametrize("bad", [entity(0, 1, "x", "unknown"), entity(2, 1, "x")])
def test_invalid_entity_is_typed_error(bad: EvalEntity) -> None:
    with pytest.raises(EvalRecordError):
        _ = api()(record("abc", [bad]), record("abc", []))
