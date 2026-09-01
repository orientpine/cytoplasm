from __future__ import annotations

import csv
import io
from dataclasses import dataclass, replace
from datetime import date
from typing import Final, Protocol, Sequence

from meeting_action_id import next_id

DB_FILENAME: Final = "action-items.csv"
FIELDS: Final = (
    "id", "project", "title", "owner", "due", "status", "opened_on", "opened_note",
    "closed_on", "closed_note", "basis",
)
OPEN: Final = "open"
DONE: Final = "done"
SUMMARY_HEADING: Final = "## Action Item 종합"
OUTSTANDING_HEADING: Final = "### 가. 미결 Action Items"
CREATED_HEADING: Final = "### 나. 신규 Action Items"


@dataclass(frozen=True, slots=True)
class Record:
    id: str
    project: str
    title: str
    owner: str
    due: str
    status: str
    opened_on: str
    opened_note: str
    closed_on: str
    closed_note: str
    basis: str


class ActionSource(Protocol):
    """Attributes extracted from a meeting action item."""

    @property
    def title(self) -> str: ...

    @property
    def deadline(self) -> str | None: ...

    @property
    def owner(self) -> str | None: ...

    @property
    def basis(self) -> str: ...


@dataclass(frozen=True, slots=True)
class NewItem:
    title: str
    owner: str = ""
    due: str = ""
    basis: str = ""


@dataclass(frozen=True, slots=True)
class MergeResult:
    records: tuple[Record, ...]
    created: tuple[Record, ...]
    closed: tuple[Record, ...]
    outstanding: tuple[Record, ...]


def _ordered(records: Sequence[Record]) -> tuple[Record, ...]:
    return tuple(sorted(records, key=lambda record: (record.opened_on, record.id)))


def load(text: str) -> tuple[Record, ...]:
    records: list[Record] = []
    try:
        for row in csv.DictReader(io.StringIO(text)):
            if not row or not row.get("id"):
                continue
            values = [row.get(field) or "" for field in FIELDS]
            records.append(Record(*values))
    except (csv.Error, TypeError):
        pass
    return tuple(records)


def dump(records: Sequence[Record]) -> str:
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    for record in _ordered(records):
        writer.writerow({field: getattr(record, field) for field in FIELDS})
    return stream.getvalue()


def _normalised(title: str) -> str:
    return " ".join(title.split()).casefold()


def merge(
    existing: Sequence[Record], *, project: str, code: str, year: int,
    new_items: Sequence[NewItem], resolved_ids: Sequence[str], note_name: str, on: date,
) -> MergeResult:
    closed: list[Record] = []
    resolved = set(resolved_ids)
    retained: list[Record] = []
    for record in existing:
        if record.id in resolved and record.status == OPEN and record.project == project:
            record = replace(record, status=DONE, closed_on=on.isoformat(), closed_note=note_name)
            closed.append(record)
        retained.append(record)
    seen = {(record.opened_note, _normalised(record.title)) for record in existing}
    created: list[Record] = []
    for item in new_items:
        if (note_name, _normalised(item.title)) in seen:
            continue
        action_id = next_id(code, year, (record.id for record in [*retained, *created]))
        record = Record(
            action_id.text, project, item.title, item.owner, item.due, OPEN, on.isoformat(),
            note_name, "", "", item.basis,
        )
        created.append(record)
    records = _ordered([*retained, *created])
    outstanding = tuple(record for record in records if record.status == OPEN and record not in created)
    return MergeResult(records, _ordered(created), _ordered(closed), outstanding)


def prompt_block(records: Sequence[Record]) -> str:
    return "\n".join(
        f"{record.id} | {record.title} | 담당 {record.owner} | 기한 {record.due}"
        for record in records
    )


def _table(records: Sequence[Record]) -> list[str]:
    if not records:
        return ["- 없음"]
    lines = ["| 관리번호 | 내용 | 조치기한 | 담당기관 |", "| --- | --- | --- | --- |"]
    for record in records:
        cells = (record.id, record.title, record.due or "—", record.owner or "—")
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    return lines


def render_sections(*, outstanding: Sequence[Record], created: Sequence[Record]) -> list[str]:
    return [
        SUMMARY_HEADING, "", OUTSTANDING_HEADING, "", *_table(outstanding), "",
        CREATED_HEADING, "", *_table(created), "",
    ]


def split_sections(lines: Sequence[str]) -> dict[str, list[str]]:
    """The inverse of ``render_sections``, and it lives here so the two cannot drift.

    A project form that names 미결 and 신규 as its own sub-sections has to fill each one
    separately; reading the tables back out of the rendered block is what lets the caller
    do that without a second rendering path.
    """
    tables: dict[str, list[str]] = {"actions_open": [], "actions_new": []}
    slot = ""
    for line in lines:
        if line == OUTSTANDING_HEADING:
            slot = "actions_open"
        elif line == CREATED_HEADING:
            slot = "actions_new"
        elif line == SUMMARY_HEADING:
            slot = ""
        elif slot and line:
            tables[slot].append(line)
    return tables


def unnumbered(items: Sequence[NewItem]) -> tuple[Record, ...]:
    """Rows for a meeting with no project — real items, but nothing to number them from.

    The owner still gets the tail; only the database and its numbering need a project.
    """
    return tuple(
        Record("—", "", item.title, item.owner, item.due, OPEN, "", "", "", "", item.basis)
        for item in items
    )


def items_from(
    todos: Sequence[ActionSource], others: Sequence[ActionSource], *, mine: str = "나",
) -> tuple[NewItem, ...]:
    mine_items = tuple(
        NewItem(item.title, mine, "" if item.deadline is None else item.deadline, item.basis)
        for item in todos
    )
    other_items = tuple(
        NewItem(item.title, item.owner or "담당 미정", "" if item.deadline is None else item.deadline, item.basis)
        for item in others
    )
    return mine_items + other_items
