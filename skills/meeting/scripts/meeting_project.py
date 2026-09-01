"""Best-effort Drive state for a project's meeting form and action-item board."""

from __future__ import annotations

import os
import sys
import unicodedata
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import meeting_action_db
import meeting_action_id
import meeting_runtime
import meeting_template

REGISTRY_FILENAME: Final = "project-codes.csv"
_FORM_TERMS: Final = ("양식", "서식", "템플릿", "template")
_FOLDER_MIME: Final = "application/vnd.google-apps.folder"


@dataclass(frozen=True, slots=True)
class Board:
    project: str
    code: str
    template: object | None
    records: tuple[object, ...]
    registry: tuple[tuple[str, str], ...]
    registry_changed: bool


def _repo(module: str):
    root = str(meeting_runtime.runtime_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    return __import__(f"automation.{module}", fromlist=["_"])


def empty_board(project: str = "") -> Board:
    """Return a board with no Drive-derived state."""
    return Board(project, "", None, (), (), False)


def _form_child(children: Sequence[object]) -> object | None:
    """Prefer a readable form; still announce an owner-supplied unsupported form."""
    named = [(child, str(child.get("name", ""))) for child in children]
    for child, name in named:
        if meeting_template.is_template_name(name):
            return child
    return next((child for child, name in named if any(term in name.casefold() for term in _FORM_TERMS)), None)


@dataclass(frozen=True, slots=True)
class PendingTranscript:
    project: str
    year: str
    file_id: str
    name: str

    @property
    def stem(self) -> str:
        return self.name[:-3] if self.name.endswith(".md") else self.name


def _folders(children: Sequence[object]) -> list[object]:
    return [child for child in children if str(child.get("mimeType", "")) == _FOLDER_MIME]


def _named(children: Sequence[object], name: str) -> object | None:
    return next(
        (child for child in children
         if unicodedata.normalize("NFC", str(child.get("name", ""))) == name),
        None,
    )


def pending_transcripts(*, client: object | None = None) -> tuple[PendingTranscript, ...]:
    """Transcripts that have no minutes of their own yet.

    The project and the year come from the transcript's own path, so neither is guessed.
    A transcript counts as processed when the project's minutes folder holds a file named
    after it (`회의록-<stem>`, the name the CLI derives from the label speechtotext passes).
    That is a NAME check, not a content hash: a transcript that is re-tidied and published
    again changes its bytes while remaining the same meeting, and hashing would make the
    skill rebuild minutes it had already written.
    """
    if client is None and os.environ.get("DRIVE_PUBLISH_ENABLED") != "1":
        return ()
    found: list[PendingTranscript] = []
    try:
        outputs = _repo("drive_outputs")
        taxonomy = _repo("drive_taxonomy")
        drive = client if client is not None else outputs.client_from_environment()
        minutes_projects = _folders(
            drive.list_children(drive.ensure_folder_path(taxonomy.category_parts("meeting")))
        )
        transcripts = drive.ensure_folder_path(taxonomy.category_parts("transcript"))
        for project in _folders(drive.list_children(transcripts)):
            name = unicodedata.normalize("NFC", str(project.get("name", "")))
            twin = _named(minutes_projects, name)
            twin_years = _folders(drive.list_children(str(twin.get("id", "")))) if twin else []
            for year in _folders(drive.list_children(str(project.get("id", "")))):
                year_name = unicodedata.normalize("NFC", str(year.get("name", "")))
                twin_year = _named(twin_years, year_name)
                published = [
                    unicodedata.normalize("NFC", str(child.get("name", "")))
                    for child in (
                        drive.list_children(str(twin_year.get("id", ""))) if twin_year else []
                    )
                ]
                for child in drive.list_children(str(year.get("id", ""))):
                    file_name = unicodedata.normalize("NFC", str(child.get("name", "")))
                    if not file_name.endswith(".md"):
                        continue
                    marker = f"회의록-{file_name[:-3]}"
                    if any(marker in existing for existing in published):
                        continue
                    found.append(
                        PendingTranscript(name, year_name, str(child.get("id", "")), file_name)
                    )
    except Exception as failure:  # noqa: BLE001 - a scan failure means no candidate, never a crash
        print(f"PENDING-SCAN-FAIL {type(failure).__name__}", file=sys.stderr)
        return ()
    return tuple(found)


def download_transcript(
    pending: PendingTranscript, dest: Path, *, client: object | None = None
) -> None:
    """Fetch one pending transcript. Raises — the caller chose this file deliberately."""
    outputs = _repo("drive_outputs")
    drive = client if client is not None else outputs.client_from_environment()
    drive.download_file(pending.file_id, dest)


def detect_project(label: str, *, client: object | None = None) -> str:
    """The existing project folder this label names, or "" when it names none.

    A project is never invented: only a folder that already exists under the minutes
    category may be chosen, so a label nobody recognises creates no folder and writes no
    ledger. Nested names resolve to the longest match; two unrelated matches resolve to
    nothing, because filing a meeting under the WRONG project is worse than filing it
    under none.
    """
    if not label or (client is None and os.environ.get("DRIVE_PUBLISH_ENABLED") != "1"):
        return ""
    text = unicodedata.normalize("NFC", label)
    try:
        outputs = _repo("drive_outputs")
        taxonomy = _repo("drive_taxonomy")
        drive = client if client is not None else outputs.client_from_environment()
        folder = drive.ensure_folder_path(taxonomy.category_parts("meeting"))
        names = [
            unicodedata.normalize("NFC", str(child.get("name", "")))
            for child in drive.list_children(folder)
            if str(child.get("mimeType", "")) == _FOLDER_MIME
        ]
    except Exception as failure:  # noqa: BLE001 - detection is optional to minutes creation
        print(f"PROJECT-DETECT-FAIL {type(failure).__name__}", file=sys.stderr)
        return ""
    found = sorted((name for name in names if name and name in text), key=len, reverse=True)
    if not found or any(other not in found[0] for other in found[1:]):
        return ""
    return found[0]


def load_board(project: str, *, sensitive: bool = False, client: object | None = None) -> Board:
    """Load one project's optional Drive state without ever blocking a meeting."""
    if not project or sensitive or (client is None and os.environ.get("DRIVE_PUBLISH_ENABLED") != "1"):
        return empty_board(project)
    template: object | None = None
    records: tuple[object, ...] = ()
    registry: dict[str, str] = {}
    code = ""
    registry_changed = False
    try:
        outputs = _repo("drive_outputs")
        taxonomy = _repo("drive_taxonomy")
        drive = client if client is not None else outputs.client_from_environment()
        folder = drive.ensure_folder_path(taxonomy.project_parts("meeting", project))
        children = tuple(drive.list_children(folder))
        with tempfile.TemporaryDirectory(prefix="meeting-board-") as tmp:
            base = Path(tmp)
            form = _form_child(children)
            if form is not None:
                name = str(form.get("name", ""))
                try:
                    local = base / name
                    drive.download_file(str(form.get("id", "")), local)
                    template = meeting_template.parse(local.read_text(encoding="utf-8"))
                except Exception:
                    print(f"TEMPLATE-UNREADABLE project={project} name={name}", file=sys.stderr)
                    raise
                if template is None:
                    print(f"TEMPLATE-UNREADABLE project={project} name={name}", file=sys.stderr)
            database = next(
                (child for child in children if str(child.get("name", "")) == meeting_action_db.DB_FILENAME),
                None,
            )
            if database is not None:
                local = base / meeting_action_db.DB_FILENAME
                drive.download_file(str(database.get("id", "")), local)
                records = meeting_action_db.load(local.read_text(encoding="utf-8"))
            local = base / REGISTRY_FILENAME
            if outputs.fetch_state_file(
                taxonomy.category_parts("meeting"), REGISTRY_FILENAME, local, client=drive
            ):
                registry = meeting_action_id.load_registry(local.read_text(encoding="utf-8"))
        code, resolved = meeting_action_id.resolve_code(project, registry)
        registry_changed = resolved != registry
        registry = resolved
    except Exception as failure:  # noqa: BLE001 - Drive state is optional to minutes creation
        print(f"BOARD-FETCH-FAIL project={project} {type(failure).__name__}", file=sys.stderr)
    return Board(project, code, template, records, tuple(registry.items()), registry_changed)


def save_board(
    board: Board, records: Sequence[object], *, client: object | None = None
) -> tuple[str, str]:
    """Persist optional board state, returning links for the writes that completed."""
    if not board.project or (client is None and os.environ.get("DRIVE_PUBLISH_ENABLED") != "1"):
        return "", ""
    db_link = ""
    registry_link = ""
    try:
        outputs = _repo("drive_outputs")
        taxonomy = _repo("drive_taxonomy")
        drive = client if client is not None else outputs.client_from_environment()
        with tempfile.TemporaryDirectory(prefix="meeting-board-") as tmp:
            base = Path(tmp)
            try:
                database = base / meeting_action_db.DB_FILENAME
                database.write_text(meeting_action_db.dump(records), encoding="utf-8")
                db_link = outputs.publish_state_file(
                    taxonomy.project_parts("meeting", board.project),
                    meeting_action_db.DB_FILENAME,
                    database,
                    client=drive,
                )
            except Exception as failure:  # noqa: BLE001 - saving state must not lose meeting minutes
                print(f"BOARD-SAVE-FAIL project={board.project} {type(failure).__name__}", file=sys.stderr)
            if board.registry_changed:
                try:
                    registry = base / REGISTRY_FILENAME
                    registry.write_text(
                        meeting_action_id.dump_registry(dict(board.registry)), encoding="utf-8"
                    )
                    registry_link = outputs.publish_state_file(
                        taxonomy.category_parts("meeting"), REGISTRY_FILENAME, registry, client=drive
                    )
                except Exception as failure:  # noqa: BLE001 - saving state must not lose meeting minutes
                    print(f"BOARD-SAVE-FAIL project={board.project} {type(failure).__name__}", file=sys.stderr)
    except Exception as failure:  # noqa: BLE001 - client setup is optional too
        print(f"BOARD-SAVE-FAIL project={board.project} {type(failure).__name__}", file=sys.stderr)
    return db_link, registry_link
