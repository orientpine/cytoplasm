"""Characterize fixed-width compatibility and URL-only boundary deviations."""

from __future__ import annotations

import random
from itertools import accumulate

import pytest

from automation.interop.chunker import chunk_message


@pytest.mark.parametrize("message", ["", "가나다" * 100, "한" * 2_000, "https://a", " 끝 \n"])
def test_chunk_message_when_within_limit_then_preserves_one_identical_chunk(message: str) -> None:
    # Given
    limit = 2_000
    # When
    chunks = chunk_message(message, limit)
    # Then
    assert chunks == [message]


@pytest.mark.parametrize("limit", [1, 7, 10, 2_000])
def test_chunk_message_when_random_non_url_input_then_matches_fixed_width(limit: int) -> None:
    # Given
    rng = random.Random(3003)
    lengths = [0, limit, 2 * limit, 5_000] + [rng.randrange(6_001) for _ in range(296)]
    messages = ["".join(rng.choices("abcXYZ019가나다한글 \n\t", k=size)) for size in lengths]
    messages.extend(["a\n" + "b" * 18, "x" * 5_000])
    expected = [[m[i : i + limit] for i in range(0, len(m), limit)] or [""] for m in messages]
    # When
    actual = [chunk_message(message, limit) for message in messages]
    # Then
    assert actual == expected


@pytest.mark.parametrize("limit", [7, 10, 2_000])
def test_chunk_message_when_random_urls_avoid_cuts_then_matches_fixed_width(limit: int) -> None:
    # Given: each URL ends at a cut; the following whitespace ends its span.
    rng = random.Random(6303)
    messages: list[str] = []
    for _ in range(100):
        blocks: list[str] = []
        for _ in range(rng.randrange(1, 5)):
            scheme = rng.choice(["http://", "https://"] if limit >= 8 else ["http://"])
            url = scheme + "".join(rng.choices("abc019한글/", k=rng.randrange(limit - len(scheme) + 1)))
            prefix = "".join(rng.choices("abcXYZ한글 \n", k=limit - len(url)))
            blocks.extend([prefix + url, " " * limit])
        messages.append("".join(blocks))
    expected = [[m[i : i + limit] for i in range(0, len(m), limit)] for m in messages]
    # When
    actual = [chunk_message(message, limit) for message in messages]
    # Then
    assert actual == expected


@pytest.mark.parametrize("limit", [1, 7, 10, 2_000])
def test_chunk_message_when_random_mixed_input_then_preserves_content_within_limit(limit: int) -> None:
    # Given
    rng = random.Random(9303)
    messages: list[str] = []
    for _ in range(100):
        text = "".join(rng.choices("abcXYZ한글 \n\t", k=rng.randrange(6_001)))
        offset = rng.randrange(len(text) + 1)
        url = rng.choice(["http://a", "https://예시/경로", "https://" + "a" * 4_001])
        messages.append(text[:offset] + url + text[offset:])
    # When
    results = [chunk_message(message, limit) for message in messages]
    # Then
    assert ["".join(chunks) for chunks in results] == messages
    assert all(len(chunk) <= limit for chunks in results for chunk in chunks)


@pytest.mark.parametrize("limit", [7, 8, 10, 32, 2_000])
@pytest.mark.parametrize("crosses", [False, True], ids=["aligned", "crossing"])
def test_chunk_message_when_generated_spans_then_preserves_urls_with_only_justified_cuts(
    limit: int, crosses: bool,
) -> None:
    # Given: the generator records spans; no production scanner is used as an oracle.
    rng = random.Random(3303)
    cases: list[tuple[str, list[tuple[int, int]]]] = []
    for _ in range(100):
        message = ""
        spans: list[tuple[int, int]] = []
        for _ in range(rng.randrange(1, 6)):
            scheme = rng.choice(["http://", "https://"] if limit >= 8 else ["http://"])
            url = scheme + "".join(rng.choices("abc019한글/", k=rng.randrange(limit - len(scheme) + 1)))
            cut = (len(message) // limit + 2) * limit
            offset = cut - rng.randrange(1, len(url)) if crosses else cut - len(url)
            message += "".join(rng.choices("abcXYZ한글 (:\n\t", k=offset - len(message)))
            spans.append((offset, offset + len(url)))
            message += url + rng.choice([" ", "\n", "\t", "　", "\x1c"])
        cases.append((message, spans))
    # When
    results = [chunk_message(message, limit) for message, _ in cases]
    # Then
    for (message, spans), chunks in zip(cases, results, strict=True):
        assert "".join(chunks) == message
        assert all(len(chunk) <= limit for chunk in chunks)
        edges = list(accumulate(map(len, chunks), initial=0))
        windows = list(zip(edges, edges[1:]))
        for url_start, url_end in spans:
            assert sum(left <= url_start < url_end <= right for left, right in windows) == 1
        fixed = [message[i : i + limit] for i in range(0, len(message), limit)]
        straddles = any(s < c < e for s, e in spans for c in range(limit, len(message), limit))
        assert straddles == crosses
        if straddles:
            assert chunks != fixed
        else:
            assert chunks == fixed
        for start, end in windows:
            natural = min(start + limit, len(message))
            if end != natural:
                assert any(end == s and start < s < natural < e and e - s <= limit for s, e in spans)


@pytest.mark.parametrize("prefix", ["x" * 1_980 + " ", "x" * 1_990 + "링크:", "x" * 1_990 + "("])
def test_chunk_message_when_url_crosses_fixed_boundary_then_rewinds_to_scheme(prefix: str) -> None:
    # Given
    url = "https://discord.com/channels/1/2/3"
    message = prefix + url
    # When
    chunks = chunk_message(message)
    # Then
    assert chunks == [prefix, url]


@pytest.mark.parametrize("limit", [1, 7, 10, 2_000])
def test_chunk_message_when_overlong_url_starts_message_then_keeps_hard_cuts(limit: int) -> None:
    # Given
    message = "https://" + "a" * 4_001
    expected = [message[i : i + limit] for i in range(0, len(message), limit)]
    # When
    chunks = chunk_message(message, limit)
    # Then
    assert chunks == expected


def test_chunk_message_when_scheme_embedded_in_overlong_span_then_keeps_hard_cuts() -> None:
    # Given
    message = "https://" + "a" * 2_002 + "https://" + "b" * 2_000
    expected = [message[i : i + 2_000] for i in range(0, len(message), 2_000)]
    # When
    chunks = chunk_message(message)
    # Then
    assert chunks == expected


@pytest.mark.parametrize("suffix", ["", " tail"])
def test_chunk_message_when_bare_scheme_exceeds_limit_then_keeps_hard_cut(suffix: str) -> None:
    # Given
    message = "xhttps://" + suffix
    # When
    chunks = chunk_message(message, 7)
    # Then
    assert chunks == ["xhttps:", "//" + suffix]


@pytest.mark.parametrize("suffix", ["", " tail"])
def test_chunk_message_when_bare_scheme_fits_limit_then_rewinds_to_scheme(suffix: str) -> None:
    # Given
    message = "xhttps://" + suffix
    # When
    chunks = chunk_message(message, 8)
    # Then
    assert chunks == ["x", "https://"] + ([suffix] if suffix else [])


@pytest.mark.parametrize("limit", [7, 8, 10, 2_000])
def test_chunk_message_when_prefixed_url_exceeds_limit_then_keeps_hard_cuts(limit: int) -> None:
    # Given: overlong by one character, with its scheme crossing the first cut.
    url = "http://" + "a" * (limit - 6)
    message = "한" * (limit - 1) + url + " tail"
    expected = [message[i : i + limit] for i in range(0, len(message), limit)]
    # When
    chunks = chunk_message(message, limit)
    # Then
    assert chunks == expected


@pytest.mark.parametrize("message", ["xhttps://a tail", "x" * 10 + "https://a tail"])
def test_chunk_message_when_cut_equals_span_edge_then_keeps_hard_cut(message: str) -> None:
    # Given: the first span ends or starts exactly at offset ten.
    limit = 10
    expected = [message[i : i + limit] for i in range(0, len(message), limit)]
    # When
    chunks = chunk_message(message, limit)
    # Then
    assert chunks == expected


@pytest.mark.parametrize("separator", [" ", "\n", "\t", "\r", "　", "\x1c"])
def test_chunk_message_when_two_urls_share_window_then_rewinds_only_crossing_span(separator: str) -> None:
    # Given
    prefix = "https://a" + separator
    second = "http://b"
    # When
    chunks = chunk_message(prefix + second, 15)
    # Then
    assert chunks == [prefix, second]


@pytest.mark.parametrize("limit", [0, -1, -2_000])
def test_chunk_message_when_limit_is_invalid_then_raises_value_error(limit: int) -> None:
    # Given
    message = "content"
    # When / Then
    with pytest.raises(ValueError):
        chunk_message(message, limit)
