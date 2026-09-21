"""Guard relative markdown links and heading anchors in governed docs."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import unquote

_REPO_ROOT: Final = Path(__file__).resolve().parents[2]
_LINK: Final = re.compile(r"(?<!!)\[(?:[^\]]*)\]\(([^)]+)\)")
_HEADING: Final = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*$")
_EXTERNAL: Final = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|/|~)", re.IGNORECASE)
_CLOSING_HASHES: Final = re.compile(r"\s+#+\s*$")
#: Paths the public export drops. The manifest itself ships, so the exported tree can
#: name what it lacks on purpose.
_EXPORT_MANIFEST: Final = _REPO_ROOT / "configs" / "public-export-manifest.txt"
#: `docs/qa/` is tracked in the development origin and manifest-excluded, so its absence
#: identifies the exported tree — the only tree where a governed link may point at a
#: file that was dropped on purpose (2026-09-21: the v1.9.6 export gate failed on 46
#: such links while every target existed in private).
_EXPORTED_TREE: Final = not (_REPO_ROOT / "docs" / "qa").is_dir()


@dataclass(frozen=True, slots=True)
class MissingTarget:
    source: Path
    line: int
    href: str

    def __str__(self) -> str:
        return f"{self.source}:{self.line}: missing file {self.href}"


@dataclass(frozen=True, slots=True)
class MissingHeading:
    source: Path
    line: int
    href: str
    anchor: str

    def __str__(self) -> str:
        return f"{self.source}:{self.line}: missing heading #{self.anchor} in {self.href}"


type BrokenLink = MissingTarget | MissingHeading


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _github_heading_slug(heading: str) -> str:
    lowered = _nfc(heading).strip().casefold()
    return "".join(
        "-"
        if character.isspace()
        else character
        if character in "-_" or unicodedata.category(character)[0] in {"L", "M", "N"}
        else ""
        for character in lowered
    )


def _visible_lines(text: str) -> tuple[tuple[int, str], ...]:
    visible: list[tuple[int, str]] = []
    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            visible.append((number, line))
    return tuple(visible)


def _heading_anchors(text: str) -> frozenset[str]:
    counts: dict[str, int] = {}
    anchors: list[str] = []
    for _, line in _visible_lines(text):
        matched = _HEADING.fullmatch(line)
        if matched is None:
            continue
        title = _CLOSING_HASHES.sub("", matched.group(1)).strip()
        base = _github_heading_slug(title)
        if not base:
            continue
        seen = counts.get(base, 0)
        counts[base] = seen + 1
        anchors.append(base if seen == 0 else f"{base}-{seen}")
    return frozenset(anchors)


def _split_href(raw: str) -> tuple[str, str] | None:
    body = raw.strip()
    if not body:
        return None
    if body[0] == "<" and body.endswith(">"):
        body = body[1:-1].strip()
    if " " in body:
        path_part, rest = body.split(None, 1)
        if rest[:1] in {'"', "'", "("}:
            body = path_part
    path_part, _, anchor = body.partition("#")
    return unquote(path_part), unquote(anchor)


def _is_checkable(path_part: str) -> bool:
    if path_part == "":
        return True
    if _EXTERNAL.match(path_part) is not None:
        return False
    if "<" in path_part or ">" in path_part:
        return False
    return True


def governed_markdown_files(root: Path) -> tuple[Path, ...]:
    """Markdown files the standing doc-link guard covers."""
    candidates = (
        root / "AGENTS.md",
        root / "docs" / "AGENTS.md",
        *sorted((root / "docs" / "guide").glob("**/*.md")),
        *sorted((root / "docs" / "기능소개").glob("**/*.md")),
        *sorted((root / "docs" / "troubleshooting").glob("**/*.md")),
    )
    return tuple(path for path in candidates if path.is_file())


def export_excluded_entries(manifest: Path) -> tuple[str, ...]:
    """Repository-relative entries the public export removes (directories end with `/`)."""
    if not manifest.is_file():
        return ()
    return tuple(
        line.strip()
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def is_export_excluded(relative: str, entries: Sequence[str]) -> bool:
    """True when the export manifest drops `relative` (a file entry or a parent directory)."""
    return any(
        relative == entry.rstrip("/") or (entry.endswith("/") and relative.startswith(entry))
        for entry in entries
    )


def dropped_by_export(issue: BrokenLink, root: Path, entries: Sequence[str]) -> bool:
    """A missing file is not a broken link when the export dropped it on purpose."""
    if not isinstance(issue, MissingTarget):
        return False
    parsed = _split_href(issue.href)
    if parsed is None:
        return False
    target = (issue.source.parent / parsed[0]).resolve()
    try:
        relative = target.relative_to(root).as_posix()
    except ValueError:
        return False
    return is_export_excluded(relative, entries)


def collect_broken_links(files: Sequence[Path]) -> tuple[BrokenLink, ...]:
    """Return broken relative markdown links and heading anchors."""
    issues: list[BrokenLink] = []
    heading_cache: dict[Path, frozenset[str]] = {}
    for source in files:
        text = source.read_text(encoding="utf-8")
        for line_number, line in _visible_lines(text):
            for matched in _LINK.finditer(line):
                href = matched.group(1)
                parsed = _split_href(href)
                if parsed is None:
                    issues.append(MissingTarget(source, line_number, href))
                    continue
                path_part, anchor = parsed
                if not _is_checkable(path_part):
                    continue
                target = source if path_part == "" else source.parent / path_part
                if not target.exists():
                    issues.append(MissingTarget(source, line_number, href))
                    continue
                if anchor == "" or target.is_dir() or target.suffix.lower() != ".md":
                    continue
                resolved = target.resolve()
                if resolved not in heading_cache:
                    heading_cache[resolved] = _heading_anchors(
                        resolved.read_text(encoding="utf-8")
                    )
                if _nfc(anchor).casefold() not in heading_cache[resolved]:
                    issues.append(MissingHeading(source, line_number, href, anchor))
    return tuple(issues)


def test_reports_a_link_to_a_missing_file(tmp_path: Path) -> None:
    # Given: a markdown file whose only link points at a path that is not on disk.
    source = tmp_path / "guide.md"
    source.write_text("[next](missing.md)\n", encoding="utf-8")

    # When: the link guard scans that file.
    issues = collect_broken_links((source,))

    # Then: the missing file is reported.
    assert any(isinstance(issue, MissingTarget) for issue in issues), (
        "link to a missing file was not reported"
    )


def test_reports_a_link_to_a_missing_heading(tmp_path: Path) -> None:
    # Given: a target file that exists but does not have the linked heading.
    target = tmp_path / "target.md"
    target.write_text("# Present\n", encoding="utf-8")
    source = tmp_path / "guide.md"
    source.write_text("[go](target.md#absent)\n", encoding="utf-8")

    # When: the link guard scans that file.
    issues = collect_broken_links((source,))

    # Then: the missing heading is reported.
    assert any(isinstance(issue, MissingHeading) for issue in issues), (
        "link to a missing heading was not reported"
    )


def test_accepts_a_relative_link_to_an_existing_korean_heading(tmp_path: Path) -> None:
    # Given: a Korean heading whose GitHub slug the link uses.
    target = tmp_path / "target.md"
    target.write_text("## 설치 절차\n", encoding="utf-8")
    source = tmp_path / "guide.md"
    source.write_text("[go](target.md#설치-절차)\n", encoding="utf-8")

    # When: the link guard scans that file.
    issues = collect_broken_links((source,))

    # Then: a resolvable relative link is silent.
    assert issues == ()


def test_skips_external_and_placeholder_hrefs(tmp_path: Path) -> None:
    # Given: http(s), mailto, /srv, and docs/qa placeholder hrefs.
    source = tmp_path / "guide.md"
    source.write_text(
        "\n".join(
            (
                "[a](https://example.com/x.md)",
                "[b](mailto:ops@example.com)",
                "[c](/srv/autophagy-private/raw.md)",
                "[d](docs/qa/<wave-id>/note.md)",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    # When: the link guard scans that file.
    issues = collect_broken_links((source,))

    # Then: none of those hrefs are treated as repo files.
    assert issues == ()


def test_duplicate_headings_receive_github_suffix(tmp_path: Path) -> None:
    # Given: two identical headings, which GitHub slugs as repeat and repeat-1.
    target = tmp_path / "target.md"
    target.write_text("# Repeat\n\n# Repeat\n", encoding="utf-8")
    source = tmp_path / "guide.md"
    source.write_text("[go](target.md#repeat-1)\n", encoding="utf-8")

    # When: the link guard scans that file.
    issues = collect_broken_links((source,))

    # Then: the suffixed duplicate anchor resolves.
    assert issues == ()


def test_same_file_fragment_without_heading_is_reported(tmp_path: Path) -> None:
    # Given: a same-file fragment that names no heading in the source.
    source = tmp_path / "guide.md"
    source.write_text("# Present\n\n[go](#absent)\n", encoding="utf-8")

    # When: the link guard scans that file.
    issues = collect_broken_links((source,))

    # Then: the missing heading is reported.
    assert any(isinstance(issue, MissingHeading) for issue in issues), (
        "same-file fragment without a heading was not reported"
    )


def test_export_dropped_targets_are_tolerated_only_when_the_manifest_names_them(tmp_path: Path) -> None:
    # Given: a guide linking to a manifest-dropped file, a dropped directory, and a plain missing file.
    source = tmp_path / "docs" / "guide" / "install.md"
    source.parent.mkdir(parents=True)
    source.write_text("[a](../qa/P0/summary.md) [b](report-hub.md) [c](missing.md)\n", encoding="utf-8")
    entries = ("docs/qa/", "docs/guide/report-hub.md")

    # When: the guard scans it and the exported-tree tolerance is applied.
    issues = collect_broken_links((source,))
    kept = tuple(issue for issue in issues if not dropped_by_export(issue, tmp_path, entries))

    # Then: only the link to a file the export never dropped survives.
    assert [issue.href for issue in issues] == ["../qa/P0/summary.md", "report-hub.md", "missing.md"]
    assert [issue.href for issue in kept] == ["missing.md"]


def test_governed_docs_have_no_broken_relative_links() -> None:
    # Given: the governed instruction tree in this checkout.
    files = governed_markdown_files(_REPO_ROOT)
    assert files, "governed markdown glob matched nothing"

    # When: the link guard scans those files — in the exported tree, links to files the
    # manifest dropped on purpose are not broken links.
    issues = collect_broken_links(files)
    if _EXPORTED_TREE:
        entries = export_excluded_entries(_EXPORT_MANIFEST)
        issues = tuple(issue for issue in issues if not dropped_by_export(issue, _REPO_ROOT, entries))

    # Then: every relative markdown link and heading fragment resolves.
    assert not issues, "\n".join(
        f"{issue.source.relative_to(_REPO_ROOT)}:{issue.line}: {type(issue).__name__} {issue.href}"
        for issue in issues
    )
