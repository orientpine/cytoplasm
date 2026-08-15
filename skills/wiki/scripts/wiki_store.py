"""Personal wiki store (W2-2): restricted frontmatter schema + read-only queries.

The vault is ``~agent/wiki`` (mode 700, OUTSIDE git). Every note's frontmatter
carries the 5 required keys (title, tags, created, updated, links) plus, since
decision-twin schema v1, an optional whitelist of typed twin keys (TWIN_KEYS).
Anything else is a schema violation and is rejected with guidance.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

REQUIRED_KEYS = ("title", "tags", "created", "updated", "links")
KIND_VALUES = ("decision", "principle", "preference", "note")
AUTHORITY_VALUES = ("strict", "default", "advisory")
PROVENANCE_VALUES = ("stated", "observed", "inferred")
STATUS_VALUES = ("active", "superseded", "archived")
TWIN_KEYS = ("kind", "authority", "provenance", "status", "review_after", "supersedes")
STALE_DAYS = 90
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLUG_RE = re.compile(r"^[0-9A-Za-z가-힣][0-9A-Za-z가-힣._-]*$")
_BARE_RE = re.compile(r"^[0-9A-Za-z가-힣._-]+$")
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")

SCHEMA_GUIDE = """\
frontmatter 스키마 안내 (필수 키 5개 정확히, 그 외 키 금지):
---
title: "노트 제목"
tags: [tag-1, 연구]
created: 2026-07-15T00:00:00Z
updated: 2026-07-15T00:00:00Z
links: [다른-노트-슬러그]
---
- title: 비어있지 않은 문자열
- tags / links: 문자열 리스트 (빈 리스트 [] 허용, 공백·경로 문자 금지)
- created / updated: UTC ISO-8601 (YYYY-MM-DDTHH:MM:SSZ)"""


class SchemaError(ValueError):
    """Frontmatter schema violation, carrying user-facing guidance lines."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Note:
    slug: str
    path: Path
    meta: dict
    body: str


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_scalar(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"'):
        value = json.loads(raw)
        if not isinstance(value, str):
            raise ValueError("quoted scalar must be a string")
        return value
    return raw


def _split_flow(inner: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    in_quote = escaped = False
    for char in inner:
        if escaped:
            buf.append(char)
            escaped = False
        elif char == "\\" and in_quote:
            buf.append(char)
            escaped = True
        elif char == '"':
            in_quote = not in_quote
            buf.append(char)
        elif char == "," and not in_quote:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    parts.append("".join(buf))
    return [part.strip() for part in parts if part.strip()]


def parse_note(text: str) -> tuple[dict, str]:
    """Parse a note into (meta, body); raise SchemaError on any violation."""
    if not text.startswith("---\n"):
        raise SchemaError(["frontmatter 블록이 없습니다 (파일이 '---' 줄로 시작해야 함)"])
    try:
        _, header, body = text.split("---\n", 2)
    except ValueError:
        raise SchemaError(["frontmatter 종료 '---' 줄이 없습니다"]) from None
    meta: dict[str, object] = {}
    for line in header.splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "\t")) or ":" not in line:
            raise SchemaError([f"'key: value' 형식이 아닌 frontmatter 줄: {line!r}"])
        key, _, raw = line.partition(":")
        key, raw = key.strip(), raw.strip()
        if key in meta:
            raise SchemaError([f"중복 키: {key}"])
        try:
            if raw.startswith("[") and raw.endswith("]"):
                meta[key] = [_parse_scalar(item) for item in _split_flow(raw[1:-1])]
            else:
                meta[key] = _parse_scalar(raw)
        except (json.JSONDecodeError, ValueError):
            raise SchemaError([f"{key}: 값의 따옴표/형식이 잘못되었습니다"]) from None
    errors = validate_meta(meta)
    if errors:
        raise SchemaError(errors)
    return meta, body


def validate_meta(meta: dict) -> list[str]:
    errors: list[str] = []
    unknown = sorted(set(meta) - set(REQUIRED_KEYS) - set(TWIN_KEYS))
    if unknown:
        errors.append("허용되지 않은 키: " + ", ".join(unknown))
    for key in REQUIRED_KEYS:
        if key not in meta:
            errors.append(f"필수 키 누락: {key}")
    title = meta.get("title")
    if "title" in meta and (not isinstance(title, str) or not title.strip()):
        errors.append("title: 비어있지 않은 문자열이어야 합니다")
    for key in ("tags", "links"):
        if key not in meta:
            continue
        value = meta[key]
        if not isinstance(value, list):
            errors.append(f"{key}: 리스트여야 합니다 (예: [a, b])")
            continue
        for item in value:
            if not isinstance(item, str) or not item.strip():
                errors.append(f"{key}: 빈 항목은 허용되지 않습니다")
            elif key == "links" and not _SLUG_RE.match(item):
                errors.append(f"links: 슬러그 형식이 아닙니다: {item!r}")
            elif key == "tags" and re.search(r"\s", item):
                errors.append(f"tags: 공백을 포함할 수 없습니다: {item!r}")
    for key in ("created", "updated"):
        value = meta.get(key)
        if key not in meta:
            continue
        if not isinstance(value, str) or not _TS_RE.match(value):
            errors.append(f"{key}: UTC ISO-8601 (YYYY-MM-DDTHH:MM:SSZ) 형식이어야 합니다")
            continue
        try:
            datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            errors.append(f"{key}: 존재하지 않는 날짜/시각입니다: {value}")
    errors.extend(_validate_twin_keys(meta))
    return errors


def _validate_twin_keys(meta: dict) -> list[str]:
    errors: list[str] = []
    twin_present = [key for key in TWIN_KEYS if key in meta]
    if twin_present and "kind" not in meta:
        errors.append(
            "kind: twin 키(" + ", ".join(twin_present) + ") 사용 시 필수입니다"
            f" (허용: {', '.join(KIND_VALUES)})"
        )
    kind = meta.get("kind")
    if "kind" in meta and (not isinstance(kind, str) or kind not in KIND_VALUES):
        errors.append(f"kind: 허용되지 않은 값: {kind!r} (허용: {', '.join(KIND_VALUES)})")
    enums = (("authority", AUTHORITY_VALUES), ("provenance", PROVENANCE_VALUES))
    if kind in ("decision", "principle", "preference"):
        for required, allowed in enums:
            if required not in meta:
                errors.append(
                    f"{required}: kind가 {kind}일 때 필수입니다 (허용: {', '.join(allowed)})"
                )
    for key, allowed in (*enums, ("status", STATUS_VALUES)):
        value = meta.get(key)
        if key in meta and (not isinstance(value, str) or value not in allowed):
            errors.append(f"{key}: 허용되지 않은 값: {value!r} (허용: {', '.join(allowed)})")
    review_after = meta.get("review_after")
    if "review_after" in meta:
        if not isinstance(review_after, str) or not _DATE_RE.match(review_after):
            errors.append("review_after: YYYY-MM-DD 형식이어야 합니다")
        else:
            try:
                datetime.strptime(review_after, "%Y-%m-%d")
            except ValueError:
                errors.append(f"review_after: 존재하지 않는 날짜입니다: {review_after}")
    supersedes = meta.get("supersedes")
    if "supersedes" in meta and (not isinstance(supersedes, str) or not _SLUG_RE.match(supersedes)):
        errors.append(f"supersedes: 슬러그 형식이 아닙니다: {supersedes!r}")
    return errors


def _dump_scalar(value: str) -> str:
    return value if _BARE_RE.match(value) else json.dumps(value, ensure_ascii=False)


def compose_note(meta: dict, body: str) -> str:
    """Serialize meta+body into canonical note text; raise SchemaError if invalid."""
    errors = validate_meta(meta)
    if errors:
        raise SchemaError(errors)
    lines = [
        "---",
        f'title: {json.dumps(meta["title"], ensure_ascii=False)}',
        "tags: [" + ", ".join(_dump_scalar(tag) for tag in meta["tags"]) + "]",
        f'created: {meta["created"]}',
        f'updated: {meta["updated"]}',
        "links: [" + ", ".join(_dump_scalar(link) for link in meta["links"]) + "]",
    ]
    for key in TWIN_KEYS:
        if key in meta:
            lines.append(f"{key}: {_dump_scalar(meta[key])}")
    lines.append("---")
    body = body.rstrip("\n")
    return "\n".join(lines) + "\n" + (body + "\n" if body else "")


def slugify(title: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z가-힣._-]+", "-", title.strip().lower())
    slug = slug[:64].strip("-.")
    if not slug or not _SLUG_RE.match(slug):
        raise SchemaError([f"title로 슬러그를 만들 수 없습니다: {title!r} (--slug로 직접 지정하세요)"])
    return slug


def note_path(root: Path, slug: str) -> Path:
    if not _SLUG_RE.match(slug):
        raise SchemaError([f"잘못된 슬러그: {slug!r}"])
    return root / f"{slug}.md"


def load_note(root: Path, slug: str) -> Note:
    path = note_path(root, slug)
    meta, body = parse_note(path.read_text(encoding="utf-8"))
    return Note(slug, path, meta, body)


def iter_notes(root: Path) -> Iterator[Note]:
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*.md")):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        try:
            meta, body = parse_note(path.read_text(encoding="utf-8"))
        except (SchemaError, OSError, UnicodeDecodeError):
            continue
        yield Note(path.stem, path, meta, body)


def query_notes(root: Path, term: str, tag: str | None = None) -> Iterator[Note]:
    needle = term.lower()
    for note in iter_notes(root):
        if tag and tag not in note.meta["tags"]:
            continue
        haystack = "\n".join(
            [note.meta["title"], " ".join(note.meta["tags"]), note.body]
        ).lower()
        if needle and needle not in haystack:
            continue
        yield note


def backlinks(root: Path, slug: str) -> Iterator[Note]:
    marker = f"[[{slug}]]"
    for note in iter_notes(root):
        if note.slug == slug:
            continue
        if slug in note.meta["links"] or marker in note.body:
            yield note


def cleanup_suggestions(root: Path, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    notes = list(iter_notes(root))
    slugs = {note.slug for note in notes}
    today = now.astimezone(timezone.utc).date()
    inbound: set[str] = set()
    for note in notes:
        inbound.update(note.meta["links"])
        inbound.update(_WIKILINK_RE.findall(note.body))
    suggestions: list[str] = []
    titles: dict[str, list[str]] = {}
    for note in notes:
        titles.setdefault(note.meta["title"].strip().lower(), []).append(note.slug)
        updated = datetime.strptime(note.meta["updated"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        if now - updated > timedelta(days=STALE_DAYS):
            suggestions.append(
                f"STALE {note.slug}: {STALE_DAYS}일 이상 미갱신 ({note.meta['updated']}) — 갱신/보관 검토"
            )
        if not note.meta["tags"]:
            suggestions.append(f"UNTAGGED {note.slug}: 태그 없음 — 태그 추가 검토")
        if note.slug not in inbound and not note.meta["links"]:
            suggestions.append(f"ORPHAN {note.slug}: 연결 링크 없음 — 연결 또는 병합 검토")
        if "review_after" in note.meta and datetime.strptime(
            note.meta["review_after"], "%Y-%m-%d"
        ).date() < today:
            suggestions.append(
                f"REVIEW-EXPIRED {note.slug}: review_after {note.meta['review_after']} 경과 — 재확인/강등 검토"
            )
        if "supersedes" in note.meta and note.meta["supersedes"] not in slugs:
            suggestions.append(
                f"SUPERSEDES-DANGLING {note.slug}: supersedes 대상 {note.meta['supersedes']} 없음 — 정리 검토"
            )
    for title, slugs in sorted(titles.items()):
        if len(slugs) > 1:
            suggestions.append(
                "DUPLICATE-TITLE " + ", ".join(sorted(slugs)) + f": 동일 제목 {title!r} — 병합 검토"
            )
    return suggestions
