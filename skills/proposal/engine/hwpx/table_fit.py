"""Content-aware column widths for the tables the pipeline generates."""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from typing import Final

from ..contracts.layout_profile import BODY_WIDTH

MIN_COLUMN_WIDTH: Final = 2_835
TABLE_CHAR_WIDTH: Final = 1_000

_TBL_START = b"<hp:tbl"
_TBL_END = b"</hp:tbl>"
_TR_START = b"<hp:tr"
_TR_END = b"</hp:tr>"
_TC_START = b"<hp:tc"
_TC_END = b"</hp:tc>"

_TABLE_ID_RE = re.compile(rb'\bid="(\d+)"')
_COL_COUNT_RE = re.compile(rb'\bcolCnt="(\d+)"')
_TABLE_SIZE_RE = re.compile(rb'(<hp:sz\b[^>]*?\bwidth=")(\d+)(")')
_CELL_SIZE_RE = re.compile(rb'(<hp:cellSz\b[^>]*?\bwidth=")(\d+)(")')
_CELL_ADDR_RE = re.compile(rb'<hp:cellAddr\b[^>]*?\bcolAddr="(\d+)"')
_CELL_SPAN_RE = re.compile(rb'<hp:cellSpan\b[^>]*?\bcolSpan="(\d+)"')
_CELL_MARGIN_RE = re.compile(rb'<hp:cellMargin\b[^>]*?\bleft="(\d+)"[^>]*?\bright="(\d+)"')
_PARAGRAPH_SPLIT_RE = re.compile(rb"<hp:p\b")
_TEXT_RE = re.compile(rb"<hp:t>(.*?)</hp:t>", re.DOTALL)
_PICTURE_WIDTH_RE = re.compile(rb'<hp:pic\b.*?<hp:sz\b[^>]*?\bwidth="(\d+)"', re.DOTALL)

_ENTITIES: Final = (
    (b"&lt;", b"<"),
    (b"&gt;", b">"),
    (b"&quot;", b'"'),
    (b"&apos;", b"'"),
    (b"&amp;", b"&"),
)
_TAG_BOUNDARY: Final = (b" ", b">", b"/", b"\t", b"\n", b"\r")
_HALF_WIDTH_LIMIT: Final = 0x1100

__all__ = ["MIN_COLUMN_WIDTH", "TABLE_CHAR_WIDTH", "fit_tables"]


def fit_tables(
    section_bytes: bytes,
    *,
    body_width: int = BODY_WIDTH,
    skip_table_ids: Collection[str] = (),
) -> bytes:
    if body_width <= 0:
        return section_bytes
    skipped = {str(table_id) for table_id in skip_table_ids}
    pieces: list[bytes] = []
    cursor = 0
    for start, end in _balanced_spans(section_bytes, _TBL_START, _TBL_END):
        table = section_bytes[start:end]
        pieces.append(section_bytes[cursor:start])
        pieces.append(table if _table_id(table) in skipped else _fit_table(table, body_width))
        cursor = end
    pieces.append(section_bytes[cursor:])
    return b"".join(pieces)


def _fit_table(table_bytes: bytes, body_width: int) -> bytes:
    column_count = _column_count(table_bytes)
    rows = _balanced_spans(table_bytes, _TR_START, _TR_END)
    if column_count < 1 or not rows:
        return table_bytes
    widths = _allocate(_column_naturals(table_bytes, rows, column_count), body_width)
    return _apply_widths(table_bytes, rows, widths)


def _allocate(naturals: Sequence[int], body_width: int) -> list[int]:
    """Split ``body_width`` in proportion to ``naturals``, pinning starved columns at the floor."""
    count = len(naturals)
    if count == 0:
        return []
    if body_width < count * MIN_COLUMN_WIDTH:
        even = body_width // count
        widths = [even] * count
        widths[-1] += body_width - even * count
        return widths

    widths = [0] * count
    pinned = [False] * count
    while True:
        free = [index for index in range(count) if not pinned[index]]
        remaining = body_width - sum(widths[index] for index in range(count) if pinned[index])
        if not free:
            widths[_widest(naturals, range(count))] += body_width - sum(widths)
            return widths
        demand = sum(naturals[index] for index in free)
        starved = [
            index
            for index in free
            if _share(remaining, naturals[index], demand, len(free)) < MIN_COLUMN_WIDTH
        ]
        if not starved:
            for index in free:
                widths[index] = _share(remaining, naturals[index], demand, len(free))
            widths[_widest(naturals, free)] += body_width - sum(widths)
            return widths
        for index in starved:
            widths[index] = MIN_COLUMN_WIDTH
            pinned[index] = True


def _share(remaining: int, natural: int, demand: int, free_count: int) -> int:
    if demand <= 0:
        return remaining // free_count
    return remaining * natural // demand


def _widest(naturals: Sequence[int], candidates: Collection[int]) -> int:
    return max(candidates, key=lambda index: (naturals[index], -index))


def _column_naturals(table_bytes: bytes, rows: Sequence[tuple[int, int]], count: int) -> list[int]:
    naturals = [0] * count
    for row_start, row_end in rows:
        row = table_bytes[row_start:row_end]
        for cell_start, cell_end in _balanced_spans(row, _TC_START, _TC_END):
            cell = row[cell_start:cell_end]
            column = _last_int(_CELL_ADDR_RE, cell)
            if column is None or column >= count or _column_span(cell) != 1:
                continue
            naturals[column] = max(naturals[column], _cell_natural(cell))
    return [natural or MIN_COLUMN_WIDTH for natural in naturals]


def _cell_natural(cell_bytes: bytes) -> int:
    margin = _last(_CELL_MARGIN_RE, cell_bytes)
    margins = int(margin.group(1)) + int(margin.group(2)) if margin is not None else 0
    text_width = _widest_paragraph_units(cell_bytes) * TABLE_CHAR_WIDTH // 2
    picture = _PICTURE_WIDTH_RE.search(cell_bytes)
    picture_width = int(picture.group(1)) if picture is not None else 0
    return max(text_width, picture_width) + margins


def _widest_paragraph_units(cell_bytes: bytes) -> int:
    paragraphs = _PARAGRAPH_SPLIT_RE.split(cell_bytes)
    return max(
        (sum(_advance_units(text) for text in _TEXT_RE.findall(paragraph)) for paragraph in paragraphs),
        default=0,
    )


def _advance_units(text_bytes: bytes) -> int:
    resolved = text_bytes
    for entity, literal in _ENTITIES:
        resolved = resolved.replace(entity, literal)
    return sum(
        1 if ord(character) < _HALF_WIDTH_LIMIT else 2
        for character in resolved.decode("utf-8", errors="replace")
    )


def _apply_widths(
    table_bytes: bytes, rows: Sequence[tuple[int, int]], widths: Sequence[int]
) -> bytes:
    changed = table_bytes
    for row_start, row_end in reversed(rows):
        row = changed[row_start:row_end]
        for cell_start, cell_end in reversed(_balanced_spans(row, _TC_START, _TC_END)):
            cell = row[cell_start:cell_end]
            column = _last_int(_CELL_ADDR_RE, cell)
            if column is None or column >= len(widths):
                continue
            span = _column_span(cell)
            width = sum(widths[column : column + span]) or widths[column]
            row = row[:cell_start] + _set_cell_width(cell, width) + row[cell_end:]
        changed = changed[:row_start] + row + changed[row_end:]
    head_end = rows[0][0]
    head = _TABLE_SIZE_RE.sub(
        rb"\g<1>" + str(sum(widths)).encode("ascii") + rb"\g<3>", changed[:head_end], count=1
    )
    return head + changed[head_end:]


def _set_cell_width(cell_bytes: bytes, width: int) -> bytes:
    match = _last(_CELL_SIZE_RE, cell_bytes)
    if match is None:
        return cell_bytes
    replacement = match.group(1) + str(width).encode("ascii") + match.group(3)
    return cell_bytes[: match.start()] + replacement + cell_bytes[match.end() :]


def _column_span(cell_bytes: bytes) -> int:
    return _last_int(_CELL_SPAN_RE, cell_bytes) or 1


def _table_id(table_bytes: bytes) -> str:
    match = _TABLE_ID_RE.search(_open_tag(table_bytes))
    return match.group(1).decode("ascii") if match is not None else ""


def _column_count(table_bytes: bytes) -> int:
    match = _COL_COUNT_RE.search(_open_tag(table_bytes))
    return int(match.group(1)) if match is not None else 0


def _open_tag(table_bytes: bytes) -> bytes:
    end = table_bytes.find(b">")
    return table_bytes[: end + 1] if end != -1 else table_bytes


def _last(pattern: re.Pattern[bytes], data: bytes) -> re.Match[bytes] | None:
    match = None
    for match in pattern.finditer(data):
        pass
    return match


def _last_int(pattern: re.Pattern[bytes], data: bytes) -> int | None:
    match = _last(pattern, data)
    return int(match.group(1)) if match is not None else None


def _balanced_spans(data: bytes, start_token: bytes, end_token: bytes) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    cursor = 0
    while True:
        open_at = _find_tag(data, start_token, cursor)
        close_at = data.find(end_token, cursor)
        if close_at == -1:
            return spans
        if open_at != -1 and open_at < close_at:
            if depth == 0:
                start = open_at
            depth += 1
            cursor = open_at + len(start_token)
            continue
        cursor = close_at + len(end_token)
        if depth > 0:
            depth -= 1
            if depth == 0:
                spans.append((start, cursor))


def _find_tag(data: bytes, token: bytes, start: int) -> int:
    index = data.find(token, start)
    while index != -1:
        boundary = data[index + len(token) : index + len(token) + 1]
        if boundary in _TAG_BOUNDARY:
            return index
        index = data.find(token, index + 1)
    return -1
