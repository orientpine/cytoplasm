"""공용 교정 엔진 — 참고 문서를 읽고, 지목된 치환과 바른 용어 근접 교정을 한다.

교정이 전사본이 아니라 산출 문서에서 일어나므로 엔진은 어느 스킬에도 속하지 않는다.
"""

from __future__ import annotations

import pytest

from automation import term_correction


def test_parse_reads_a_two_column_table_a_canonical_term_and_a_legacy_pair() -> None:
    parsed = term_correction.parse_glossary(
        "# 이 줄은 메모\n틀린표기,올바른표기\n열기환기,열교환기\n한전기술\n영무=업무\n"
    )

    assert parsed == (("열기환기", "열교환기"), ("한전기술", "한전기술"), ("영무", "업무"))


def test_parse_keeps_a_comma_that_sheets_quoted() -> None:
    assert term_correction.parse_glossary('"서울, 부산",수도권\n') == (("서울, 부산", "수도권"),)


def test_the_pairs_the_owner_wrote_are_replaced_as_written() -> None:
    fixed, corrections = term_correction.apply("영무 보고입니다", (("영무", "업무"),))

    assert fixed == "업무 보고입니다"
    assert [(c.before, c.after, c.kind) for c in corrections] == [
        ("영무", "업무", term_correction.EXACT)
    ]


def test_a_mishearing_the_owner_never_wrote_down_is_still_corrected() -> None:
    """바른 용어 한 줄만 적어도 잡는다 — 틀리는 방식은 모델이 정하지 소유자가 아니다."""
    fixed, corrections = term_correction.apply("항정기술 담당자", (("한전기술", "한전기술"),))

    assert fixed == "한전기술 담당자"
    assert [(c.before, c.after, c.kind) for c in corrections] == [
        ("항정기술", "한전기술", term_correction.FUZZY)
    ]


def test_a_word_that_is_already_spelled_right_is_left_alone() -> None:
    fixed, corrections = term_correction.apply("한전기술 담당자", (("한전기술", "한전기술"),))

    assert fixed == "한전기술 담당자"
    assert corrections == ()


def test_two_terms_that_are_both_close_leave_the_word_untouched() -> None:
    """동전던지기로 고르면 그 문장은 조용히 틀린다."""
    fixed, corrections = term_correction.apply(
        "항정기술 담당자", (("한전기술", "한전기술"), ("한정기술", "한정기술"))
    )

    assert fixed == "항정기술 담당자"
    assert corrections == ()


def test_a_two_syllable_term_never_repairs_by_distance() -> None:
    fixed, corrections = term_correction.apply("영무 보고입니다", (("업무", "업무"),))

    assert fixed == "영무 보고입니다"
    assert corrections == ()


def test_the_candidate_is_the_head_of_the_word_never_its_middle() -> None:
    """「성금=선금」이 「기성금」을 깨뜨린 사고 — 후보는 언제나 어절 앞머리다."""
    fixed, _ = term_correction.apply("중도계약금 지급", (("계약금", "계약금"),))

    assert fixed == "중도계약금 지급"


def test_the_hint_lists_the_correct_spellings_only() -> None:
    hint = term_correction.prompt_hint((("영무", "업무"), ("한전기술", "한전기술")))

    assert hint == "고유명사: 업무, 한전기술"


def test_an_empty_glossary_changes_nothing() -> None:
    assert term_correction.apply("항정기술", ()) == ("항정기술", ())
    assert term_correction.prompt_hint(()) == ""


def test_jamo_distance_counts_a_transposition_once() -> None:
    """고신뢰성/고실내성은 ㄹㄴ 이 자리를 맞바꾼 전치다."""
    left = term_correction.jamo("고신뢰성")
    right = term_correction.jamo("고실내성")

    assert term_correction.distance(left, right) <= term_correction.tolerance(left)


@pytest.mark.parametrize("row", ["", "   ", "# 주석만", "틀린표기,올바른표기"])
def test_a_note_or_a_header_is_not_an_entry(row: str) -> None:
    assert term_correction.parse_glossary(row + "\n") == ()
