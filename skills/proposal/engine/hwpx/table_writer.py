from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from xml.etree import ElementTree as ET

from ..contracts import AnchorMap, KPI
from ..contracts.validators import validate_kpi_sum
from .anchor_map import NAMESPACES

HP_TBL_START = b"<hp:tbl"
HP_TBL_END = b"</hp:tbl>"
HP_TR_START = b"<hp:tr"
HP_TR_END = b"</hp:tr>"
HP_TC_START = b"<hp:tc"
HP_TC_END = b"</hp:tc>"
HP_T_START = b"<hp:t"
HP_T_END = b"</hp:t>"
HEADER_ROWS = 1
_CELL_WIDTH_RE = re.compile(rb'<hp:cellSz width="(\d+)" height="')

ComparisonKind = Literal["prior-research", "tech-gap"]
_COMPARISON_TITLES: dict[ComparisonKind, str] = {
    "prior-research": "선행연구 비교",
    "tech-gap": "기술 Gap 분석",
}


GANTT_MONTHS = 12
# The 추진 내용 column is one thirteenth of the table; a planner that emits a whole
# sentence as a work package title turns the row into an unreadable block.
GANTT_LABEL_MAX = 24
# The seed sizes 추진 내용 like a month cell, so an activity name wraps one
# character per line. A month cell only ever holds "12" or a mark, so the column
# is given a third of the table and the twelve months share the rest evenly.
GANTT_LABEL_COLUMN_SHARE = 0.34
# 소유자 지시(2026-08-28): 추진 내용은 연차마다 연구 꼭지 8개로 세우고, 그 8개의
# 월 배분이 연차를 남김없이 덮어야 "이 표대로 하면 과제가 끝난다"가 성립한다.
GANTT_ACTIVITIES_PER_YEAR = 8
GANTT_ANCHOR_SLOT = "gantt.row.0.month.1"


@dataclass(frozen=True, slots=True)
class GanttRow:
    """One 추진 내용 activity and the first-year months it occupies."""

    label: str
    months: frozenset[int]


@dataclass(frozen=True)
class TableData:
    header: tuple[str, str, str]
    rows: tuple[tuple[str, str, str], ...]


__all__ = [
    "GANTT_ACTIVITIES_PER_YEAR",
    "GANTT_ANCHOR_SLOT",
    "GANTT_LABEL_MAX",
    "GANTT_MONTHS",
    "GanttRow",
    "TableData",
    "_find_table_rows",
    "gantt_table_id",
    "insert_comparison_table",
    "validate_table_occupancy",
    "validate_gantt_years",
    "write_gantt",
    "write_gantt_years",
    "write_kpi_table",
]


def gantt_table_id(anchor_map: AnchorMap) -> str:
    return _anchored_table_id(anchor_map, GANTT_ANCHOR_SLOT)


def write_kpi_table(xml_bytes: bytes, kpis: list[KPI], anchor_map: AnchorMap) -> bytes:
    validate_kpi_sum(kpis)
    table_id = _anchored_table_id(anchor_map, "kpi.row.0.name")
    resized = _resize_data_rows(xml_bytes, table_id, len(kpis))
    rows = _find_table_rows_by_id(resized, table_id)[HEADER_ROWS:]
    replacements = [
        _kpi_cell_values(kpi, len(_find_cells(resized[row_start:row_end])))
        for kpi, (row_start, row_end) in zip(kpis, rows, strict=True)
    ]
    written = _write_rows(resized, table_id, replacements, start_row=HEADER_ROWS)
    header_start, header_end = _find_table_rows_by_id(written, table_id)[0]
    header = _kpi_header(len(_find_cells(written[header_start:header_end])))
    written = _write_rows(written, table_id, [header], start_row=0)
    written = _set_table_page_break(written, table_id, "TABLE")
    return _drop_table_line_layout(written, table_id)


def write_gantt(xml_bytes: bytes, rows: Sequence[GanttRow], anchor_map: AnchorMap) -> bytes:
    """Fill 추진 내용 with one named row per activity.

    Column 0 carries the activity name and columns 1..12 the months of the first
    project year — the shape the seed's own row already has. Writing thirteen
    month values instead shifts every month by one, overwrites the name with a
    mark, and leaves the seed's "…" placeholder row behind.
    """
    if not rows:
        raise ValueError("추진 내용 requires at least one activity row")
    invalid = sorted(
        month for row in rows for month in row.months if month < 1 or month > GANTT_MONTHS
    )
    if invalid:
        raise ValueError(f"months must be in 1..{GANTT_MONTHS}: {invalid}")

    table_id = gantt_table_id(anchor_map)
    resized = _resize_data_rows(xml_bytes, table_id, len(rows))
    values = [
        [
            _clipped_label(row.label),
            *("■" if month in row.months else " " for month in range(1, GANTT_MONTHS + 1)),
        ]
        for row in rows
    ]
    written = _write_rows(resized, table_id, values, start_row=HEADER_ROWS)
    written = _balance_gantt_columns(written, table_id)
    return _drop_table_line_layout(written, table_id)


def validate_gantt_years(years: Sequence[tuple[int, Sequence[GanttRow]]]) -> None:
    """연차별 추진 내용의 완결성 검증 — 렌더와 로더가 같은 정의를 쓴다.

    연차는 1부터 연속이어야 하고, 연차마다 꼭지가 정확히 8개, 월은 1..12 안이며,
    채워진 달은 1월부터 끊기지 않아야 한다. 마지막 연차만 12월 전에 끝날 수 있다
    (과제가 연 중간에 종료되는 경우) — 그 외 연차의 빈 달은 계획 공백이다.
    """
    if not years:
        raise ValueError("추진 내용 requires at least one year")
    year_numbers = [year for year, _ in years]
    if year_numbers != list(range(1, len(years) + 1)):
        raise ValueError(f"연차가 1부터 연속이어야 합니다 (contiguous years), got {year_numbers}")
    for position, (year, rows) in enumerate(years):
        if len(rows) != GANTT_ACTIVITIES_PER_YEAR:
            raise ValueError(
                f"{year}차년도 연구 꼭지가 {len(rows)}개 — 연차마다 정확히 "
                f"{GANTT_ACTIVITIES_PER_YEAR}개가 필요합니다"
            )
        covered: set[int] = set()
        for row in rows:
            if not row.months:
                raise ValueError(f"{year}차년도 '{row.label}' 에 배정된 달이 없습니다")
            invalid = sorted(month for month in row.months if month < 1 or month > GANTT_MONTHS)
            if invalid:
                raise ValueError(f"months must be in 1..{GANTT_MONTHS}: {invalid}")
            covered.update(row.months)
        last_year = position == len(years) - 1
        required_through = max(covered) if last_year else GANTT_MONTHS
        missing = sorted(set(range(1, required_through + 1)) - covered)
        if missing:
            raise ValueError(f"{year}차년도에 비는 달이 있습니다 (uncovered): {missing}")


def write_gantt_years(
    xml_bytes: bytes,
    years: Sequence[tuple[int, Sequence[GanttRow]]],
    anchor_map: AnchorMap,
) -> bytes:
    """추진 내용을 전 연차에 걸쳐 채운다 — 연차 구분행 하나에 꼭지 8행씩.

    시드 표는 "(1차년도만 기입)" 안내를 달고 있으므로 다년으로 채울 때 그 안내도
    함께 갱신한다 — 표가 3개 연차를 실었는데 머리말이 1차년도만이라 말하면
    심사자가 표를 오독한다.
    """
    validate_gantt_years(years)
    values: list[list[str]] = []
    for year, rows in years:
        values.append([f"{year}차년도", *[" "] * GANTT_MONTHS])
        values.extend(
            [
                _clipped_label(row.label),
                *("■" if month in row.months else " " for month in range(1, GANTT_MONTHS + 1)),
            ]
            for row in rows
        )
    table_id = gantt_table_id(anchor_map)
    resized = _resize_data_rows(xml_bytes, table_id, len(values))
    written = _write_rows(resized, table_id, values, start_row=HEADER_ROWS)
    written = _balance_gantt_columns(written, table_id)
    written = written.replace(
        "(1차년도만 기입)".encode(), "(전 연차 기입)".encode(), 1
    )
    return _drop_table_line_layout(written, table_id)


def _balance_gantt_columns(xml_bytes: bytes, table_id: str) -> bytes:
    rows = _find_table_rows_by_id(xml_bytes, table_id)
    if not rows:
        return xml_bytes
    widths = [
        int(value)
        for value in _CELL_WIDTH_RE.findall(xml_bytes[rows[0][0] : rows[0][1]])
    ]
    if len(widths) != GANTT_MONTHS + 1:
        return xml_bytes
    total = sum(widths)
    label = int(total * GANTT_LABEL_COLUMN_SHARE)
    month = (total - label) // GANTT_MONTHS
    columns = [label, *[month] * (GANTT_MONTHS - 1), total - label - month * (GANTT_MONTHS - 1)]

    changed = xml_bytes
    for row_start, row_end in reversed(rows):
        row = changed[row_start:row_end]
        cells = _find_cells(row)
        if len(cells) != len(columns):
            continue
        for index in reversed(range(len(cells))):
            cell_start, cell_end = cells[index]
            resized_cell = _CELL_WIDTH_RE.sub(
                f'<hp:cellSz width="{columns[index]}"'.encode("ascii") + b' height="',
                row[cell_start:cell_end],
                count=1,
            )
            row = row[:cell_start] + resized_cell + row[cell_end:]
        changed = changed[:row_start] + row + changed[row_end:]
    return changed


def _clipped_label(label: str) -> str:
    collapsed = " ".join(label.split())
    if len(collapsed) <= GANTT_LABEL_MAX:
        return collapsed
    return collapsed[: GANTT_LABEL_MAX - 1] + "…"


def insert_comparison_table(
    xml_bytes: bytes,
    data: TableData,
    *,
    kind: ComparisonKind,
    anchor: str = "kpi.row.0.name",
    anchor_map: AnchorMap | None = None,
) -> bytes:
    """Insert a three-column comparison table cloned from the anchored seed table.

    Requests are never truncated: callers may intentionally render tables longer than one page.
    An empty data body is rejected because it cannot constitute a comparison table.
    """
    if kind not in _COMPARISON_TITLES:
        raise ValueError(f"unsupported comparison table kind: {kind}")
    if not data.rows:
        raise ValueError("comparison table requires at least one row")
    _validate_three_columns(data.header, "header")
    for row_index, row in enumerate(data.rows, start=1):
        _validate_three_columns(row, f"row {row_index}")

    if anchor_map is None:
        from .anchor_map import load_anchor_map

        anchor_map = load_anchor_map()
    source_table_id = _anchored_table_id(anchor_map, anchor)
    source_start, source_end = _table_offsets_by_id(xml_bytes, source_table_id)
    clone = xml_bytes[source_start:source_end]

    rows = _find_table_rows_in_range(clone, 0, len(clone))
    if len(rows) < 2:
        raise ValueError("seed table clone requires at least two rows")
    title_template = clone[rows[0][0] : rows[0][1]]
    body_template = clone[rows[1][0] : rows[1][1]]
    generated_rows = [
        _comparison_row(title_template, [_COMPARISON_TITLES[kind]], 0, merged=True, header=True),
        _comparison_row(title_template, list(data.header), 1, merged=False, header=True),
    ]
    generated_rows.extend(
        _comparison_row(body_template, list(values), row_index, merged=False, header=False)
        for row_index, values in enumerate(data.rows, start=2)
    )
    clone = clone[: rows[0][0]] + b"".join(generated_rows) + clone[rows[-1][1] :]

    table_ids = [
        int(match.group(1))
        for match in re.finditer(rb'<hp:tbl\b[^>]*\bid="(\d+)"', xml_bytes)
    ]
    z_orders = [
        int(match.group(1))
        for match in re.finditer(rb'<hp:tbl\b[^>]*\bzOrder="(\d+)"', xml_bytes)
    ]
    if not table_ids or not z_orders:
        raise ValueError("section contains no numbered seed table")
    new_table_id = str(max(table_ids) + 1)
    clone = _set_start_tag_attr(clone, HP_TBL_START, "id", new_table_id)
    clone = _set_start_tag_attr(clone, HP_TBL_START, "zOrder", str(max(z_orders) + 1))
    clone = _set_start_tag_attr(clone, HP_TBL_START, "rowCnt", str(len(generated_rows)))
    clone = _set_start_tag_attr(clone, HP_TBL_START, "colCnt", "3")
    clone = _set_start_tag_attr(clone, HP_TBL_START, "noAdjust", "0")
    clone = _set_start_tag_attr(clone, HP_TBL_START, "pageBreak", "TABLE")
    clone = _set_start_tag_attr(clone, HP_TBL_START, "repeatHeader", "1")
    clone = re.sub(rb"<hp:linesegarray\b[^>]*>.*?</hp:linesegarray>", b"", clone, flags=re.DOTALL)

    changed = xml_bytes[:source_start] + clone + xml_bytes[source_start:]
    changed_root = ET.fromstring(changed)
    inserted = next(
        (
            table
            for table in changed_root.findall(".//hp:tbl", NAMESPACES)
            if table.attrib.get("id") == new_table_id
        ),
        None,
    )
    if inserted is None:
        raise ValueError("inserted comparison table could not be resolved by id")
    validate_table_occupancy(inserted)
    return changed


def validate_table_occupancy(table: ET.Element) -> None:
    """Validate that every declared table coordinate is occupied exactly once."""
    try:
        row_count = int(table.attrib["rowCnt"])
        col_count = int(table.attrib["colCnt"])
    except (KeyError, ValueError) as exc:
        raise ValueError("table rowCnt/colCnt must be integers") from exc
    rows = table.findall("./hp:tr", NAMESPACES)
    if len(rows) != row_count:
        raise ValueError(f"rowCnt mismatch: declared {row_count}, actual {len(rows)}")
    if row_count < 1 or col_count < 1:
        raise ValueError("table rowCnt/colCnt must be positive")

    occupancy = [[0 for _ in range(col_count)] for _ in range(row_count)]
    for cell in table.findall("./hp:tr/hp:tc", NAMESPACES):
        address = cell.find("./hp:cellAddr", NAMESPACES)
        span = cell.find("./hp:cellSpan", NAMESPACES)
        if address is None or span is None:
            raise ValueError("table cell is missing cellAddr or cellSpan")
        try:
            row_addr = int(address.attrib["rowAddr"])
            col_addr = int(address.attrib["colAddr"])
            row_span = int(span.attrib["rowSpan"])
            col_span = int(span.attrib["colSpan"])
        except (KeyError, ValueError) as exc:
            raise ValueError("table cell address/span must be integers") from exc
        if row_span < 1 or col_span < 1:
            raise ValueError("table cell spans must be positive")
        if row_addr < 0 or col_addr < 0 or row_addr + row_span > row_count or col_addr + col_span > col_count:
            raise ValueError("table cell address/span is outside the declared grid")
        for row_index in range(row_addr, row_addr + row_span):
            for col_index in range(col_addr, col_addr + col_span):
                occupancy[row_index][col_index] += 1

    overlaps = sum(value > 1 for row in occupancy for value in row)
    gaps = sum(value == 0 for row in occupancy for value in row)
    if overlaps or gaps:
        raise ValueError(f"invalid table occupancy: overlap={overlaps}, gap={gaps}")


def _validate_three_columns(values: Sequence[object], label: str) -> None:
    if len(values) != 3:
        raise ValueError(f"comparison table {label} must contain exactly 3 columns")
    if not all(isinstance(value, str) for value in values):
        raise ValueError(f"comparison table {label} values must be strings")


def _comparison_row(
    template: bytes,
    values: list[str],
    row_addr: int,
    *,
    merged: bool,
    header: bool,
) -> bytes:
    changed = template
    cells = _find_cells(changed)
    if len(cells) != 3:
        raise ValueError(f"seed comparison row must contain 3 cells, found {len(cells)}")
    if merged:
        total_width = sum(_cell_width(changed[start:end]) for start, end in cells)
        first_start, first_end = cells[0]
        first_cell = changed[first_start:first_end]
        first_cell = _set_all_attr(first_cell, "colAddr", "0")
        first_cell = _set_all_attr(first_cell, "rowAddr", str(row_addr))
        first_cell = _set_all_attr(first_cell, "colSpan", "3")
        first_cell = _set_all_attr(first_cell, "rowSpan", "1")
        first_cell = _set_all_attr(first_cell, "width", str(total_width), tag=b"hp:cellSz")
        changed = changed[:first_start] + first_cell + changed[cells[-1][1] :]
        cells = _find_cells(changed)
    else:
        for col_addr in reversed(range(3)):
            cell_start, cell_end = cells[col_addr]
            cell = changed[cell_start:cell_end]
            cell = _set_all_attr(cell, "colAddr", str(col_addr))
            cell = _set_all_attr(cell, "rowAddr", str(row_addr))
            cell = _set_all_attr(cell, "colSpan", "1")
            cell = _set_all_attr(cell, "rowSpan", "1")
            changed = changed[:cell_start] + cell + changed[cell_end:]
            cells = _find_cells(changed)
    changed = re.sub(
        rb'(<hp:tc\b[^>]*\bheader=")[01](")',
        rb"\g<1>" + (b"1" if header else b"0") + rb"\g<2>",
        changed,
    )
    return _write_cells(changed, values)


def _cell_width(cell: bytes) -> int:
    match = re.search(rb'<hp:cellSz\b[^>]*\bwidth="(\d+)"', cell)
    if match is None:
        raise ValueError("seed table cell is missing numeric width")
    return int(match.group(1))


def _set_all_attr(xml_bytes: bytes, attr: str, value: str, *, tag: bytes | None = None) -> bytes:
    encoded_attr = re.escape(attr.encode("ascii"))
    encoded_value = value.encode("ascii")
    if tag is None:
        pattern = rb"(" + encoded_attr + rb'=\")\d+(\")'
    else:
        pattern = rb"(<" + re.escape(tag) + rb"\b[^>]*\b" + encoded_attr + rb'=\")\d+(\")'
    return re.sub(pattern, rb"\g<1>" + encoded_value + rb"\g<2>", xml_bytes)


def _set_table_page_break(xml_bytes: bytes, table_id: str, value: str) -> bytes:
    start, end = _table_offsets_by_id(xml_bytes, table_id)
    changed = _set_start_tag_attr(xml_bytes[start:end], HP_TBL_START, "pageBreak", value)
    return xml_bytes[:start] + changed + xml_bytes[end:]


def _set_start_tag_attr(xml_bytes: bytes, token: bytes, attr: str, value: str) -> bytes:
    tag_end = xml_bytes.find(b">", _find_start_tag(xml_bytes, token, 0))
    if tag_end == -1:
        raise ValueError(f"malformed {token.decode('ascii')} start tag")
    start_tag = xml_bytes[: tag_end + 1]
    pattern = rb"(\b" + re.escape(attr.encode("ascii")) + rb'=\")[^\"]*(\")'
    changed, count = re.subn(pattern, rb"\g<1>" + value.encode("ascii") + rb"\g<2>", start_tag, count=1)
    if count != 1:
        raise ValueError(f"table start tag is missing {attr}")
    return changed + xml_bytes[tag_end + 1 :]


def _anchored_table_id(anchor_map: AnchorMap, slot: str) -> str:
    try:
        node_ref = anchor_map.slots[slot]
    except KeyError as exc:
        raise KeyError(f"unknown table anchor slot: {slot}") from exc
    if node_ref.ancestor_table_id is None:
        raise ValueError(f"anchor slot has no ancestor table id: {slot}")
    return node_ref.ancestor_table_id


def _find_table_rows(xml_bytes: bytes, table_index: int) -> list[tuple[int, int]]:
    table_start, table_end = _table_offsets(xml_bytes, table_index)
    return _find_table_rows_in_range(xml_bytes, table_start, table_end)


def _find_table_rows_by_id(xml_bytes: bytes, table_id: str) -> list[tuple[int, int]]:
    table_start, table_end = _table_offsets_by_id(xml_bytes, table_id)
    return _find_table_rows_in_range(xml_bytes, table_start, table_end)


def _find_table_rows_in_range(
    xml_bytes: bytes, table_start: int, table_end: int
) -> list[tuple[int, int]]:
    rows: list[tuple[int, int]] = []
    search_from = table_start
    while True:
        row_start = _find_start_tag(xml_bytes, HP_TR_START, search_from)
        if row_start == -1 or row_start >= table_end:
            break
        row_end = xml_bytes.find(HP_TR_END, row_start, table_end)
        if row_end == -1:
            raise ValueError("malformed hp:tr: missing end tag")
        row_end += len(HP_TR_END)
        rows.append((row_start, row_end))
        search_from = row_end
    return rows


def _table_offsets(xml_bytes: bytes, table_index: int) -> tuple[int, int]:
    if table_index < 0:
        raise ValueError("table_index must be non-negative")
    start = -1
    search_from = 0
    for _ in range(table_index + 1):
        start = _find_start_tag(xml_bytes, HP_TBL_START, search_from)
        if start == -1:
            raise ValueError(f"could not find hp:tbl index {table_index}")
        search_from = start + len(HP_TBL_START)
    end = xml_bytes.find(HP_TBL_END, search_from)
    if end == -1:
        raise ValueError("malformed hp:tbl: missing end tag")
    return start, end + len(HP_TBL_END)


def _table_offsets_by_id(xml_bytes: bytes, table_id: str) -> tuple[int, int]:
    pattern = re.compile(rb'<hp:tbl\b[^>]*\bid="' + re.escape(table_id.encode("ascii")) + rb'"')
    match = pattern.search(xml_bytes)
    if match is None:
        raise ValueError(f"could not find anchored hp:tbl id {table_id}")
    end = xml_bytes.find(HP_TBL_END, match.start())
    if end == -1:
        raise ValueError("malformed hp:tbl: missing end tag")
    return match.start(), end + len(HP_TBL_END)


def _resize_data_rows(xml_bytes: bytes, table_id: str, target_data_rows: int) -> bytes:
    if target_data_rows < 0:
        raise ValueError("target_data_rows must be non-negative")
    rows = _find_table_rows_by_id(xml_bytes, table_id)
    if not rows:
        raise ValueError(f"table {table_id} has no rows")
    current_data_rows = max(0, len(rows) - HEADER_ROWS)
    changed = xml_bytes
    if target_data_rows > current_data_rows:
        template_start, template_end = rows[-1]
        clones = [
            _with_row_addr(xml_bytes[template_start:template_end], data_index + HEADER_ROWS)
            for data_index in range(current_data_rows, target_data_rows)
        ]
        changed = xml_bytes[:template_end] + b"".join(clones) + xml_bytes[template_end:]
    elif target_data_rows < current_data_rows:
        keep_rows = HEADER_ROWS + target_data_rows
        changed = xml_bytes[: rows[keep_rows][0]] + xml_bytes[rows[-1][1] :]

    table_start, table_end = _table_offsets_by_id(changed, table_id)
    table = _set_start_tag_attr(
        changed[table_start:table_end],
        HP_TBL_START,
        "rowCnt",
        str(HEADER_ROWS + target_data_rows),
    )
    return changed[:table_start] + table + changed[table_end:]


def _write_rows(
    xml_bytes: bytes, table_id: str, row_values: list[list[str]], start_row: int
) -> bytes:
    changed = xml_bytes
    for row_index in reversed(range(len(row_values))):
        rows = _find_table_rows_by_id(changed, table_id)
        row_start, row_end = rows[start_row + row_index]
        changed_row = _write_cells(changed[row_start:row_end], row_values[row_index])
        changed = changed[:row_start] + changed_row + changed[row_end:]
    return changed


def _write_cells(row_bytes: bytes, values: list[str]) -> bytes:
    changed = row_bytes
    cells = _find_cells(changed)
    if len(values) > len(cells):
        raise ValueError(f"row has {len(cells)} cells, cannot write {len(values)} values")
    for cell_index in reversed(range(len(values))):
        cell_start, cell_end = cells[cell_index]
        changed_cell = _write_cell_text(changed[cell_start:cell_end], values[cell_index])
        changed = changed[:cell_start] + changed_cell + changed[cell_end:]
    return changed


def _find_cells(row_bytes: bytes) -> list[tuple[int, int]]:
    cells: list[tuple[int, int]] = []
    search_from = 0
    while True:
        cell_start = _find_start_tag(row_bytes, HP_TC_START, search_from)
        if cell_start == -1:
            break
        cell_end = row_bytes.find(HP_TC_END, cell_start)
        if cell_end == -1:
            raise ValueError("malformed hp:tc: missing end tag")
        cell_end += len(HP_TC_END)
        cells.append((cell_start, cell_end))
        search_from = cell_end
    return cells


def _drop_table_line_layout(xml_bytes: bytes, table_id: str) -> bytes:
    """Discard the cache of the paragraph holding a table whose row count changed."""
    rows = _find_table_rows_by_id(xml_bytes, table_id)
    if not rows:
        return xml_bytes
    table_end = xml_bytes.find(b"</hp:tbl>", rows[-1][1])
    if table_end == -1:
        return xml_bytes
    table_end += len(b"</hp:tbl>")
    paragraph_end = xml_bytes.find(b"</hp:p>", table_end)
    if paragraph_end == -1:
        return xml_bytes
    region = xml_bytes[table_end:paragraph_end]
    return xml_bytes[:table_end] + _strip_linesegarray(region) + xml_bytes[paragraph_end:]


def _strip_linesegarray(fragment: bytes) -> bytes:
    return re.sub(
        rb"<hp:linesegarray\b[^>]*>.*?</hp:linesegarray>", b"", fragment, flags=re.DOTALL
    )


def _write_cell_text(cell_bytes: bytes, text: str) -> bytes:
    escaped = _escape_text(text).encode("utf-8")
    text_start = _find_start_tag(cell_bytes, HP_T_START, 0)
    if text_start != -1:
        text_end = cell_bytes.find(HP_T_END, text_start)
        if text_end == -1:
            raise ValueError("malformed hp:t: missing end tag")
        payload_start = cell_bytes.find(b">", text_start, text_end)
        if payload_start == -1:
            raise ValueError("malformed hp:t start tag")
        changed = cell_bytes[: payload_start + 1] + escaped + cell_bytes[text_end:]
        return _strip_linesegarray(changed)
    run_match = re.search(rb"<hp:run\b[^>]*/>", cell_bytes)
    if run_match is None:
        raise ValueError("cell has no hp:t and no self-closing hp:run")
    run_tag = run_match.group(0)
    replacement = run_tag[:-2] + b"><hp:t>" + escaped + b"</hp:t></hp:run>"
    changed = cell_bytes[: run_match.start()] + replacement + cell_bytes[run_match.end() :]
    return _strip_linesegarray(changed)


def _range_with_unit(baseline: str, target: str, unit: str) -> str:
    """A baseline-to-target range, without repeating a unit the values carry.

    Planners emit "6%" and "3%" with unit "%", so appending the unit renders
    "6% → 3% %".
    """
    span = f"{baseline} → {target}"
    if not unit or baseline.endswith(unit) or target.endswith(unit):
        return span
    return f"{span} {unit}"


# The seed's first table is the form's 단계별 목표 (구분 | 연차 | 목표). It carries the KPIs,
# so its header has to name what the cells now hold — the owner's 2026-09-30 evaluation
# deducted for a 성과지표 table whose columns said 연차·목표 over 측정 방법 text.
_KPI_HEADERS: dict[int, list[str]] = {
    3: ["성과지표", "현 수준 → 목표 (가중치)", "측정 방법 · 시험 환경 · 설정 근거"],
    5: ["성과지표", "현 수준 → 목표", "단위", "측정 방법", "가중치 · 시험 환경 · 설정 근거"],
    8: ["성과지표", "단위", "현 수준", "목표", "가중치(%)", "측정 방법", "시험 환경", "설정 근거"],
}


def _kpi_header(cell_count: int) -> list[str]:
    for width in sorted(_KPI_HEADERS, reverse=True):
        if cell_count >= width:
            return _KPI_HEADERS[width]
    return _KPI_HEADERS[3][:cell_count]


# Plans written before 2026-09-30 carry a derived rationale that only restates the range
# cell; it is dropped so re-rendering an old version does not state the target twice.
_RESTATED_RANGE_RE = re.compile(r"^현 수준 .+ 대비 목표 .+ 로 설정한다$")


def _joined(*parts: str) -> str:
    return " / ".join(
        part for part in parts if part.strip() and not _RESTATED_RANGE_RE.match(part.strip())
    )


def _kpi_cell_values(kpi: KPI, cell_count: int) -> list[str]:
    fields = [
        kpi.name,
        kpi.unit,
        kpi.baseline,
        kpi.target,
        str(kpi.weight),
        kpi.method,
        kpi.env,
        kpi.rationale,
    ]
    if cell_count >= len(fields):
        return fields
    if cell_count >= 5:
        return [
            kpi.name,
            _range_with_unit(kpi.baseline, kpi.target, kpi.unit),
            kpi.unit,
            kpi.method,
            _joined(f"{kpi.weight}%", kpi.env, kpi.rationale),
        ][:cell_count]
    if cell_count == 3:
        return [
            kpi.name,
            f"{_range_with_unit(kpi.baseline, kpi.target, kpi.unit)} ({kpi.weight}%)",
            _joined(kpi.method, kpi.env, kpi.rationale),
        ]
    return fields[:cell_count]


def _with_row_addr(row_bytes: bytes, row_addr: int) -> bytes:
    return re.sub(rb'rowAddr="\d+"', f'rowAddr="{row_addr}"'.encode("ascii"), row_bytes)


def _find_start_tag(xml_bytes: bytes, start_token: bytes, search_from: int) -> int:
    candidate = search_from
    while True:
        candidate = xml_bytes.find(start_token, candidate)
        if candidate == -1:
            return -1
        next_byte_index = candidate + len(start_token)
        if next_byte_index < len(xml_bytes) and xml_bytes[next_byte_index] in (
            ord(" "),
            ord(">"),
            ord("/"),
        ):
            return candidate
        candidate = next_byte_index


def _escape_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


