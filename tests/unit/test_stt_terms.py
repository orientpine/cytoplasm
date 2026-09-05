"""바른 용어만 적어 두면 전사본이 스스로 고쳐진다 — 그 교정의 경계.

현장 근거(2026-09-05): 실제 회의 전사본(91,894 B · 본문 37,718자 · 9,625 어절)은 1:1
용어집(틀린표기=올바른표기)으로 이미 교정된 상태였는데도, 그 용어집의 **바른 용어 4개**
만으로 다시 훑으니 1:1 이 놓친 오인식이 7회 남아 있었다 — 열기환기·열기완기(→열교환기),
항정기술(→한전기술). 소유자가 모델이 틀리는 방식을 미리 다 알 수는 없기 때문이다.

같은 훑기에서 무관한 낱말을 고친 일은 0건이었다. 이 파일은 그 정밀도를 만드는 가드를
고정한다: 세 음절 미만은 손대지 않고, 음절 수가 다르면 후보가 아니며, 두 용어에 동시에
가까우면 아무것도 하지 않고, 낱말 속 조각이 아니라 어절 앞머리만 고친다.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "speechtotext"
sys.path.insert(0, str(SKILL / "scripts"))

import stt_polish  # noqa: E402
import stt_terms  # noqa: E402

#: 노드의 실제 과제 용어집이 적어 둔 바른 용어들(틀린 표기는 적지 않았다).
REAL_TERMS = ("한전기술", "고신뢰성", "열교환기", "포스텍")


def test_a_correct_term_repairs_a_mishearing_nobody_wrote_down() -> None:
    """1:1 용어집이 놓친 오인식 — 실제 전사본에서 측정된 셋이다."""
    text = "열기환기 성능과 열기완기 배치를 항정기술 담당자와 논의했다"

    fixed, corrections = stt_terms.correct(text, REAL_TERMS)

    assert fixed == "열교환기 성능과 열교환기 배치를 한전기술 담당자와 논의했다"
    assert len(corrections) == 3


def test_a_particle_after_the_term_survives_the_repair() -> None:
    """한국어는 조사가 붙는다 — 어절 전체가 아니라 앞머리만 고쳐야 한다."""
    fixed, corrections = stt_terms.correct("항정기술에서 열기환기를 봤다", REAL_TERMS)

    assert fixed == "한전기술에서 열교환기를 봤다"
    assert len(corrections) == 2


def test_a_spelling_that_is_already_right_is_left_alone() -> None:
    """맞는 말을 고치면 그것이 곧 훼손이다."""
    assert stt_terms.correct("한전기술이 열교환기를 만든다", REAL_TERMS) == (
        "한전기술이 열교환기를 만든다",
        (),
    )


def test_a_term_shorter_than_three_syllables_is_never_guessed_at() -> None:
    """두 음절은 이웃이 너무 많다 — 그런 교정은 명시 쌍으로만 한다."""
    assert stt_terms.correct("영무를 나눴다", ("업무",)) == ("영무를 나눴다", ())
    # 거리로는 닿는 이웃(업부/업무는 자모 하나 차이)이라, 이 줄을 막는 것은 음절 수 가드뿐이다.
    assert stt_terms.correct("업부를 나눴다", ("업무",)) == ("업부를 나눴다", ())


def test_a_candidate_close_to_two_terms_is_left_alone() -> None:
    """둘 중 하나를 동전던지기로 고르면 그 문장은 조용히 틀린다."""
    fixed, corrections = stt_terms.correct("김포시청 앞에서", ("강포시청", "김표시청"))

    assert (fixed, corrections) == ("김포시청 앞에서", ())


def test_a_span_of_a_different_syllable_count_is_not_a_candidate() -> None:
    """오인식은 음절 수를 지킨다 — 길이가 다르면 다른 말이다."""
    assert stt_terms.correct("고신뢰 확보", ("고신뢰성",)) == ("고신뢰 확보", ())


def test_the_substring_hazard_the_owner_warned_about_does_not_come_back() -> None:
    """소유자 경고: 「성금=선금」 이 핵심어 「기성금」 을 「기선금」 으로 깨뜨렸다.

    퍼지 교정은 어절 앞머리에만 걸리므로 낱말 속 조각을 건드리지 않는다.
    """
    assert stt_terms.correct("기성금을 청구했다", ("선금",)) == ("기성금을 청구했다", ())
    assert stt_terms.correct("총계약금이 늘었다", ("계약금",)) == ("총계약금이 늘었다", ())
    # 대조: 같은 사고를 1:1 쌍으로 적으면 지금도 그대로 재현된다.
    broken, by_pair = stt_polish.apply_glossary("기성금을 청구했다", (("성금", "선금"),))
    assert (broken, len(by_pair)) == ("기선금을 청구했다", 1)


def test_spacing_and_punctuation_survive_untouched() -> None:
    """교정은 낱말을 바꾸는 일이지 문장을 다시 짜는 일이 아니다."""
    fixed, _ = stt_terms.correct("  항정기술,  그리고\t열기환기.", REAL_TERMS)

    assert fixed == "  한전기술,  그리고\t열교환기."


def test_a_one_column_row_is_a_correct_term() -> None:
    """바른 용어만 적어도 용어집이다 — 틀리는 방식을 미리 알 필요가 없다."""
    parsed = stt_polish.parse_glossary("한전기술\n영무,업무\n# 각주\n")

    assert parsed == (("한전기술", "한전기술"), ("영무", "업무"))


def test_the_prompt_hint_carries_a_one_column_term() -> None:
    """전사 전 힌트가 첫 방어선이다 — 고치기 전에 애초에 맞게 듣게 한다."""
    hint = stt_polish.prompt_hint(stt_polish.parse_glossary("한전기술\n열교환기\n"))

    assert "한전기술" in hint
    assert "열교환기" in hint


def test_an_explicit_pair_still_replaces_exactly() -> None:
    """소유자가 이미 쓴 1:1 쌍은 퍼지가 닿지 못하는 먼 오인식을 덮는다."""
    glossary = stt_polish.parse_glossary("영무=업무\n")

    fixed, corrections = stt_polish.apply_glossary("영무 보고", glossary)

    assert (fixed, len(corrections)) == ("업무 보고", 1)


def test_a_one_column_term_repairs_through_the_glossary_facade() -> None:
    """치환 경계는 하나다 — 스킬은 apply_glossary 만 부른다."""
    glossary = stt_polish.parse_glossary("열교환기\n")

    fixed, corrections = stt_polish.apply_glossary("열기환기 점검", glossary)

    assert (fixed, len(corrections)) == ("열교환기 점검", 1)
