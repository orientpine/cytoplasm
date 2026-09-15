"""Owner-run, one-shot rename of legacy lifelog notes to the 2026-09-15 naming.

``<날짜>-<슬러그>--<12hex>.md`` notes become ``YYYY-MM-DD HHMM <제목>.md`` (note_paths). The
new stem comes from each note's own frontmatter (``created`` for the time, ``title`` for the
words); notes whose title was the old stem itself fall back to the slug, and notes with no
usable title are reported so the owner names them with ``--stem OLD=NEW``. Dry-run is the
default: ``--apply`` performs ``git mv`` + title/H1 rewrite in the vault and, given
``--state``, moves the node's ``note_relpath`` for **written** records only — a pending
record's approval card is bound to its old path and must not move under it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Final

from .lifelog_fields import yaml_scalar
from .note_paths import STEM_RE, FALLBACK_TITLE, note_stem, stem_title, strip_recording_stamp
from .store import load_state, save_state

LIFELOG_ROOT: Final = Path("000_PARA/Area/Lifelog")
_LEGACY_DIGEST_RE: Final = re.compile(r"--[0-9a-f]{12}$")
_LEGACY_STAMP_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}-(?:\d{6}-|\d{2}-\d{2}-)?")
_TRAILING_DATE_RE: Final = re.compile(r"\s*\(\d{4}-\d{2}-\d{2}\)$")
_NO_TITLE: Final = "no title — pass --stem"
_NO_CREATED: Final = "no frontmatter created — pass --stem"


@dataclass(frozen=True, slots=True)
class Rename:
    relpath: Path
    new_name: str

    @property
    def old_name(self) -> str:
        return self.relpath.name

    @property
    def new_relpath(self) -> Path:
        return self.relpath.with_name(self.new_name)


@dataclass(frozen=True, slots=True)
class Skip:
    old_name: str
    reason: str


def plan(vault: Path, *, overrides: Mapping[str, str]) -> tuple[tuple[Rename, ...], tuple[Skip, ...]]:
    for old_name, stem in overrides.items():
        if STEM_RE.fullmatch(stem) is None:
            raise ValueError(f"--stem {old_name}: {stem!r} is not a `YYYY-MM-DD HHMM <제목>` stem")
    renames: list[Rename] = []
    skips: list[Skip] = []
    claimed: dict[Path, str] = {}
    legacy = [
        path
        for path in sorted((vault / LIFELOG_ROOT).glob("*/*.md"))
        if STEM_RE.fullmatch(path.stem) is None
    ]
    derived_first = sorted(legacy, key=lambda path: path.name in overrides)
    for path in derived_first:
        stem = overrides.get(path.name) or _derive_stem(path.read_text(encoding="utf-8"), path.stem)
        if stem in (_NO_TITLE, _NO_CREATED):
            skips.append(Skip(path.name, stem))
            continue
        new_path = path.with_name(f"{stem}.md")
        if new_path.exists() or new_path in claimed:
            skips.append(Skip(path.name, f"collides with {new_path.name}"))
            continue
        claimed[new_path] = path.name
        renames.append(Rename(path.relative_to(vault), new_path.name))
    return tuple(renames), tuple(skips)


def _derive_stem(text: str, old_stem: str) -> str:
    fields = _frontmatter(text)
    try:
        created = datetime.fromisoformat(fields.get("created", ""))
    except ValueError:
        return _NO_CREATED
    title = strip_recording_stamp(_TRAILING_DATE_RE.sub("", _unquote(fields.get("title", ""))))
    if _LEGACY_DIGEST_RE.search(title) or not any(character.isalpha() for character in title):
        slug = _LEGACY_DIGEST_RE.sub("", old_stem)
        title = strip_recording_stamp(_LEGACY_STAMP_RE.sub("", slug).replace("-", " "))
    words = stem_title(title)
    if words == FALLBACK_TITLE:
        return _NO_TITLE
    return note_stem(created, words)


def _frontmatter(text: str) -> dict[str, str]:
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            return fields
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip()
    return {}


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def rewrite_note(text: str, new_stem: str) -> str:
    lines = text.split("\n")
    body_start = 0
    if lines and lines[0] == "---":
        for index in range(1, len(lines)):
            if lines[index] == "---":
                body_start = index + 1
                break
            if lines[index].startswith("title:"):
                lines[index] = f"title: {yaml_scalar(new_stem)}"
    for index in range(body_start, len(lines)):
        if lines[index].startswith("# "):
            lines[index] = f"# {new_stem}"
            break
    return "\n".join(lines)


def apply(vault: Path, renames: Sequence[Rename], *, mapping_path: Path) -> None:
    mapping: dict[str, str] = {}
    for rename in renames:
        old = vault / rename.relpath
        new = vault / rename.new_relpath
        text = old.read_text(encoding="utf-8")
        subprocess.run(
            ["git", "mv", "--", str(rename.relpath), str(rename.new_relpath)],
            cwd=vault, check=True, capture_output=True, text=True,
        )
        _ = new.write_text(rewrite_note(text, rename.new_relpath.stem), encoding="utf-8")
        subprocess.run(
            ["git", "add", "--", str(rename.new_relpath)],
            cwd=vault, check=True, capture_output=True, text=True,
        )
        mapping[rename.relpath.as_posix()] = rename.new_relpath.as_posix()
    _ = mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def migrate_state(
    state_path: Path, mapping: Mapping[str, str]
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    state = load_state(state_path)
    records = dict(state.records)
    moved: list[str] = []
    skipped: list[tuple[str, str]] = []
    for key, record in state.records.items():
        new_relpath = mapping.get(record.note_relpath)
        if new_relpath is None:
            continue
        if record.status != "written":
            skipped.append((key, f"status {record.status} — approval card is bound to the old path"))
            continue
        records[key] = replace(record, note_relpath=new_relpath, note_title=Path(new_relpath).stem)
        moved.append(key)
    if moved:
        save_state(state_path, replace(state, records=records))
    return tuple(moved), tuple(skipped)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _ = parser.add_argument("--vault", type=Path, help="Obsidian vault checkout to rename in")
    _ = parser.add_argument("--mapping", type=Path, default=Path("lifelog-rename-mapping.json"),
                            help="old→new relpath JSON: written with --vault, read without it")
    _ = parser.add_argument("--state", type=Path, help="node plaud-sync state.json to migrate")
    _ = parser.add_argument("--stem", action="append", default=[], metavar="OLD.md=NEW STEM")
    _ = parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.vault is None and args.state is None:
        parser.error("give --vault and/or --state")
    out = sys.stdout
    print("APPLY" if args.apply else "DRY-RUN (pass --apply to rename)", file=out)
    mapping: dict[str, str] = {}
    if args.vault is not None:
        overrides = dict(item.split("=", 1) for item in args.stem if "=" in item)
        try:
            renames, skips = plan(args.vault, overrides=overrides)
        except ValueError as error:
            print(f"BAD-STEM {error}", file=sys.stderr)
            return 2
        for rename in renames:
            print(f"{rename.old_name} -> {rename.new_name}", file=out)
            mapping[rename.relpath.as_posix()] = rename.new_relpath.as_posix()
        for skip in skips:
            print(f"SKIP {skip.old_name}: {skip.reason}", file=out)
        if args.apply:
            apply(args.vault, renames, mapping_path=args.mapping)
            print(f"RENAMED {len(renames)} · mapping {args.mapping}", file=out)
    else:
        mapping = dict(json.loads(args.mapping.read_text(encoding="utf-8")))
    if args.state is not None:
        if not args.apply:
            print(f"STATE {args.state}: {len(mapping)} path(s) would be checked", file=out)
            return 0
        moved, skipped = migrate_state(args.state, mapping)
        print(f"STATE moved {len(moved)}: {' '.join(moved)}", file=out)
        for key, reason in skipped:
            print(f"STATE-SKIP {key}: {reason}", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
