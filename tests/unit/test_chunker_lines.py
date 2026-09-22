"""`chunk_lines`: 줄 경계로 채우되 URL 을 자르지 않고, 머리글 몫까지 예산에서 뺀다.

`chunk_message` 와 한 벌이다 — 예산보다 긴 줄은 그 함수가 자르므로 URL 규칙이 저절로 따라온다.
"""
from __future__ import annotations

from typing import Final

import pytest

from automation.interop.chunker import chunk_lines

_URL: Final = "https://example.test/" + "b" * 40


def _header(index: int, total: int) -> str:
    return f"[p {index}/{total}]"


def test_chunk_lines_when_the_body_fits_then_one_piece_carries_the_header() -> None:
    # Given
    body = "첫 줄\n둘째 줄"
    # When
    pieces = chunk_lines(body, limit=100, header=_header)
    # Then
    assert pieces == (f"[p 1/1]\n{body}",)


def test_chunk_lines_when_a_long_line_holds_a_url_then_the_url_stays_whole() -> None:
    # Given: 예산 경계를 가로지르도록 놓인 URL 한 개 — 자르면 어느 쪽도 클릭되지 않는다.
    limit = 100
    budget = limit - len(_header(2, 2)) - 1
    body = "가" * (budget - 10) + _URL
    # When
    pieces = chunk_lines(body, limit=limit, header=_header)
    # Then
    assert sum(_URL in piece for piece in pieces) == 1


def test_chunk_lines_when_the_count_reaches_two_digits_then_every_piece_fits() -> None:
    # Given: 조각 수가 열을 넘겨 머리글이 한 글자 길어지는 본문 — 예산이 되먹임된다.
    body = "\n".join(f"- 줄 {index:02d} " + "나" * 10 for index in range(40))
    limit = 60
    # When
    pieces = chunk_lines(body, limit=limit, header=_header)
    # Then: 통 수는 머리글이 말하는 수와 같고, 모든 통이 한도 안이며, 줄은 하나도 안 사라진다.
    assert len(pieces) >= 10
    assert all(len(piece) <= limit for piece in pieces)
    assert all(
        piece.startswith(f"{_header(index, len(pieces))}\n")
        for index, piece in enumerate(pieces, start=1)
    )
    assert "\n".join(piece.split("\n", 1)[1] for piece in pieces) == body


def test_chunk_lines_when_the_header_eats_the_whole_limit_then_it_refuses() -> None:
    # Given: 머리글만으로 한도가 차 본문이 들어갈 자리가 없는 설정.
    # When / Then: 조용히 버리는 대신 닫는다.
    with pytest.raises(ValueError):
        _ = chunk_lines("본문", limit=len(_header(1, 1)), header=_header)
