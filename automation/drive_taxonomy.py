"""Naming and placement rules for the single Drive outputs tree (pure logic).

Owns the ONLY registry of output categories so no caller hardcodes a folder
name: ``<root>/<category>/<YYYY>/<date-prefixed file | bundle folder>/<file>``
with the root counted as depth 1 and depth 5 as the hard ceiling (a bundle's
own files sit at the ceiling; shallower placements such as a budget sheet at
``예산/<YYYY>/<sheet>`` stay legal). Gate-only kinds (patent) are refused here
— they belong to their dedicated export gate.

No Drive I/O lives in this module; ``automation.drive_outputs`` is the publish
facade that turns these names into gws calls.

Two layouts share the registry. The legacy one above is the default. With
``DRIVE_LAYOUT=para`` the same kinds land in the owner's PARA tree at the top of
My Drive instead (owner 2026-11-01 Q427-Q429): a project's minutes, documents,
proposals and glossary sit in ``1_Project/<YYYY>_<project>/``, transcripts,
recordings, mail attachments and shared files pile up by year under
``4_Archive/``, ongoing work goes to ``2_Area/``, reference glossaries to
``3_Resource/`` and anything without a project to ``0_Inbox/분류대기/<YYYY>``.
The switch is flipped on a node only after the existing folders were moved, so
the next save never recreates an old-name folder.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal, TypeAlias, get_args

Periodicity: TypeAlias = Literal["weekly", "oneshot", "monthly"]
_PERIODICITIES: Final = frozenset(get_args(Periodicity))

OUTPUTS_ROOT_ENV: Final = "DRIVE_OUTPUTS_ROOT"
DEFAULT_OUTPUTS_ROOT: Final = "autophagy"
MAX_DEPTH: Final = 5

LAYOUT_ENV: Final = "DRIVE_LAYOUT"
PARA_LAYOUT: Final = "para"
INBOX: Final = "0_Inbox"
PROJECTS: Final = "1_Project"
AREAS: Final = "2_Area"
RESOURCES: Final = "3_Resource"
ARCHIVE: Final = "4_Archive"
UNSORTED: Final = (INBOX, "분류대기")
GLOSSARY_HOME: Final = (RESOURCES, "용어집")
#: PARA has no single root folder but one more level for ongoing work
#: (``2_Area/연구동향/주간동향/<YYYY>/<bundle>/<file>``).
PARA_MAX_DEPTH: Final = 6
_PROJECT_FOLDER: Final = re.compile(r"^(\d{4})_(.+)$")


class TaxonomyError(ValueError):
    """A kind, periodicity or path breaks the outputs convention (fail closed)."""


@dataclass(frozen=True, slots=True)
class Category:
    """One output category. ``skill_owned`` names the command that must produce it.

    A category a skill owns cannot be published by hand: the skill's pipeline is where
    the artifact gets its side effects (a meeting's action-item ledger and its
    management numbers, a transcript's glossary pass). A document that skipped the
    pipeline looks identical in the folder and is missing exactly what the pipeline
    adds. The registry carries the fact; ``drive_publish_cli`` enforces it, and the
    facade does NOT — the skills themselves publish through the facade.
    """

    folder: str
    periodicity: Periodicity
    gate_only: bool = False
    always_bundle: bool = False
    skill_owned: str = ""
    #: PARA placement: the yearly home without a project, the subfolder inside the
    #: project folder with one ("" = the project only names the file, the kind stays
    #: in its archive home).
    para_home: tuple[str, ...] = UNSORTED
    para_in_project: str = ""


CATEGORIES: Final[dict[str, Category]] = {
    "report": Category(
        folder="주간동향", periodicity="weekly", always_bundle=True,
        para_home=(AREAS, "연구동향", "주간동향"), para_in_project="보고서",
    ),
    "proposal": Category(folder="제안서", periodicity="oneshot", para_in_project="제안서"),
    "budget": Category(
        folder="예산", periodicity="monthly", para_home=(AREAS, "예산"), para_in_project="예산",
    ),
    "meeting": Category(
        folder="회의록", periodicity="oneshot",
        skill_owned="skills/meeting/scripts/meeting_cli.py ingest --project <과제명>",
        para_in_project="회의록",
    ),
    "transcript": Category(
        folder="전사본", periodicity="oneshot",
        skill_owned="skills/speechtotext/scripts/speechtotext_cli.py",
        para_home=(ARCHIVE, "전사본"),
    ),
    "audio": Category(
        folder="녹음원본", periodicity="oneshot",
        skill_owned="plaud_sync",
        para_home=(ARCHIVE, "녹음원본"),
    ),
    "procurement": Category(
        folder="구매", periodicity="oneshot",
        para_home=(AREAS, "행정-결재", "구매"), para_in_project="구매",
    ),
    "doctype": Category(folder="문서", periodicity="oneshot", para_in_project="문서"),
    "patent": Category(folder="특허", periodicity="oneshot", gate_only=True),
    # A file the owner sends a named person through the mail approval card (2026-10-14).
    # The upload happens only after that card's ✅, so a hand publish has no place here.
    "share": Category(
        folder="공유", periodicity="oneshot",
        skill_owned="skills/mail/scripts/triage_cli.py compose --drive-share <파일> --share-with <주소>",
        para_home=(ARCHIVE, "공유한-파일"),
    ),
}
#: Kinds whose PARA file name carries the project because their folder does not.
_PROJECT_IN_NAME: Final = frozenset({"transcript", "audio"})


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def outputs_root() -> str:
    """Name of the single Drive root folder holding every published output (legacy layout)."""
    return _nfc(os.environ.get(OUTPUTS_ROOT_ENV) or DEFAULT_OUTPUTS_ROOT)


def para_enabled() -> bool:
    """True when this node writes the PARA tree (``DRIVE_LAYOUT=para``)."""
    return (os.environ.get(LAYOUT_ENV) or "").strip().lower() == PARA_LAYOUT


def max_depth() -> int:
    return PARA_MAX_DEPTH if para_enabled() else MAX_DEPTH


def category(kind: str) -> Category:
    """Registry lookup — gate-only kinds resolve here (the migrator moves them)."""
    found = CATEGORIES.get(kind)
    if found is None:
        raise TaxonomyError(f"unknown outputs kind: {kind!r}")
    return found


def _open_kind(kind: str) -> Category:
    found = category(kind)
    if found.gate_only:
        raise TaxonomyError(
            f"{kind!r} is gate-only — publish it through its dedicated gate",
        )
    return found


def project_slug(project: str) -> str:
    """The project as one PARA name segment: spaces and ``_`` become ``-``.

    ``_`` separates the year from the project in a folder name and the date from the
    project in a transcript name, so it cannot appear inside the project itself.
    A ``YYYY_`` folder name gives back its project part.
    """
    named = _nfc(project).strip()
    matched = _PROJECT_FOLDER.match(named)
    if matched is not None:
        named = matched.group(2)
    slug = "-".join(part for part in re.split(r"[\s_]+", named) if part)
    if not slug or "/" in slug or "\\" in slug:
        raise TaxonomyError(f"invalid project segment: {project!r}")
    return slug


def project_of_folder(name: str) -> str | None:
    """The project slug a ``1_Project`` child names, or None for anything else."""
    matched = _PROJECT_FOLDER.match(_nfc(name).strip())
    if matched is None:
        return None
    try:
        return project_slug(matched.group(2))
    except TaxonomyError:
        return None


def project_folder_name(project: str, *, year: int, existing: Iterable[str] = ()) -> str:
    """``<start year>_<project>``: an existing folder of the same project wins.

    A project started in 2026 keeps its 2026 folder when a 2027 meeting is filed, so
    the caller passes the names already under ``1_Project``; only a project with no
    folder yet takes ``year``. A name that already carries its year is kept as is.
    """
    named = _nfc(project).strip()
    if _PROJECT_FOLDER.match(named) is not None:
        project_slug(named)
        return named
    slug = project_slug(named)
    for candidate in existing:
        if project_of_folder(candidate) == slug:
            return _nfc(candidate).strip()
    return f"{year:04d}_{slug}"


def projects_parts() -> tuple[str, ...]:
    """The PARA folder that holds one folder per project."""
    return (PROJECTS,)


def legacy_category_parts(kind: str) -> tuple[str, ...]:
    """Where the legacy layout keeps a kind — read-only fallback while PARA settles in."""
    return (outputs_root(), _nfc(_open_kind(kind).folder))


def legacy_project_parts(kind: str, project: str) -> tuple[str, ...]:
    """The legacy project folder of a kind — read-only fallback while PARA settles in."""
    _open_kind(kind)
    named = _nfc(project).strip()
    if not named or "/" in named or "\\" in named:
        raise TaxonomyError(f"invalid project segment: {project!r}")
    return (*legacy_category_parts(kind), named)


def registry_parts() -> tuple[str, ...]:
    """Folder of the project code registry (``project-codes.csv``)."""
    return projects_parts() if para_enabled() else legacy_category_parts("meeting")


def share_root() -> tuple[str, ...]:
    """The folder every approved share must sit under."""
    if para_enabled():
        return _open_kind("share").para_home
    return (outputs_root(), _nfc(category("share").folder))


def dated(kind: str, project: str | None = None) -> bool:
    """True when ``folder_parts`` ends with a year folder."""
    found = _open_kind(kind)
    return not (para_enabled() and project and found.para_in_project)


def name_prefix(kind: str, project: str | None = None) -> str:
    """What goes before the date: PARA archive kinds lead with the project.

    Owner Q429 "4_Archive/전사본/연도에 두고 이름 앞에 과제명": the year folder holds every
    project's transcripts, so the project comes first (``<project>_<YYYY-MM-DD>_<title>``) and
    one project's files sort together. Everything else keeps the date in front.
    """
    if para_enabled() and project and kind in _PROJECT_IN_NAME:
        return _nfc(f"{project_slug(project)}_")
    return ""


def period_key(periodicity: Periodicity, on: date) -> str:
    """Date prefix for a category: ISO week, month or day."""
    if periodicity not in _PERIODICITIES:
        raise TaxonomyError(f"unknown periodicity: {periodicity!r}")
    if periodicity == "weekly":
        iso = on.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if periodicity == "monthly":
        return f"{on.year:04d}-{on.month:02d}"
    return on.isoformat()


def category_parts(kind: str) -> tuple[str, ...]:
    """State-file folder parts for a kind: root / category (without a year).

    PARA: the kind's yearly home (``4_Archive/전사본``); a project kind whose files live
    in project folders answers ``1_Project``.
    """
    found = _open_kind(kind)
    if para_enabled():
        return projects_parts() if kind == "meeting" else found.para_home
    return (outputs_root(), _nfc(found.folder))


def folder_parts(kind: str, year: int, *, project: str | None = None) -> tuple[str, ...]:
    """Publish-path folders for a kind: root / category / [project] / year.

    The project segment is data, like the year — not a registry entry. Only the
    category's folder NAME belongs to the registry, and a research project is not a
    category: transcripts and minutes of one project belong together, and the owner's
    glossary for that project lives beside them. Callers that do not name a project
    get exactly the path they got before, which is what keeps the other six skills
    publishing through this facade untouched.
    """
    found = _open_kind(kind)
    if para_enabled():
        if project and found.para_in_project:
            return project_parts(kind, project, year=year)
        if project is not None:
            project_slug(project)  # an invalid segment is refused in both layouts
            if kind not in _PROJECT_IN_NAME:
                # A named share folder (`compose --drive-folder`) stays a folder in the archive.
                return (*found.para_home, _nfc(project).strip(), str(year))
        return (*found.para_home, str(year))
    if project is not None:
        return (*project_parts(kind, project), str(year))
    return (outputs_root(), _nfc(found.folder), str(year))


def project_parts(kind: str, project: str, *, year: int | None = None) -> tuple[str, ...]:
    """The project folder itself — root / category / project.

    One research project's transcripts, its minutes and the owner's glossary for it
    belong together, so a caller must be able to name that folder without re-deriving
    the tree by hand. The category name still comes from the registry.

    PARA: ``1_Project/<YYYY>_<project>/<kind subfolder>``. Pass the folder name
    ``drive_outputs.resolve_project`` found; a bare name takes ``year`` (default: this
    year) as its start year.
    """
    found = _open_kind(kind)
    if para_enabled():
        folder = project_folder_name(project, year=year or date.today().year)
        sub = (_nfc(found.para_in_project),) if found.para_in_project else ()
        return (*projects_parts(), folder, *sub)
    named = _nfc(project).strip()
    if not named or "/" in named or "\\" in named:
        raise TaxonomyError(f"invalid project segment: {project!r}")
    return (outputs_root(), _nfc(found.folder), named)


def artifact_name(period: str, title: str, suffix: str, *, prefix: str = "") -> str:
    """File name with the date prefix that keeps re-runs on a single copy.

    ``prefix`` is ``name_prefix(kind, project)``: empty except for PARA archive kinds.
    """
    return _nfc(f"{prefix}{period}_{title}{suffix}")


def bundle_name(period: str, title: str, *, prefix: str = "") -> str:
    """Folder name for a multi-artifact output — the file name without a suffix."""
    return artifact_name(period, title, "", prefix=prefix)


def ensure_depth(parts: Sequence[str]) -> tuple[str, ...]:
    """Return the NFC path parts, refusing anything past the depth ceiling."""
    ceiling = max_depth()
    if len(parts) > ceiling:
        joined = "/".join(parts)
        raise TaxonomyError(
            f"outputs depth {len(parts)} exceeds the maximum of {ceiling}: {joined}",
        )
    return tuple(_nfc(part) for part in parts)
