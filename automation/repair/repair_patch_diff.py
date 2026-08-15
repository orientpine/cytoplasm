"""Parse a unified diff into the per-file summary an owner approval shows.

Split out of ``repair_patch_binding`` to stay under the repository's 250
pure-LOC ceiling. Anything this parser cannot describe truthfully is refused
rather than under-reported: an omitted file would be an unapproved change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

_DEV_NULL: Final = "/dev/null"
_GIT_HEADER: Final = re.compile(r"^diff --git a/(.*) b/(.*)$")
_COMBINED: Final = ("diff --cc ", "diff --combined ")
_FORBIDDEN_IN_PATH: Final = ("\x00", "\r", "\n")
_OCTAL_DIGITS: Final = frozenset("01234567")
_ESCAPES: Final = {
    "\\": "\\", '"': '"', "a": "\a", "b": "\b", "f": "\f",
    "n": "\n", "r": "\r", "t": "\t", "v": "\v",
}


class PatchBindingError(ValueError):
    """The patch cannot be summarised honestly, so it must not be approved."""


@dataclass(frozen=True, slots=True)
class PatchFileDelta:
    """One file a patch touches, with the line counts shown to the owner."""

    old_path: str | None
    new_path: str | None
    insertions: int
    deletions: int



def assert_repo_relative(path: str) -> None:
    if not path or path.startswith("/") or any(part in path for part in _FORBIDDEN_IN_PATH):
        raise PatchBindingError("repair patch path is absolute, empty, or contains control characters")
    if ".." in path.split("/"):
        raise PatchBindingError("repair patch path escapes the repository")



@dataclass(slots=True)
class _Section:
    """Mutable accumulator for the file currently being read out of the diff."""

    old_path: str | None = None
    new_path: str | None = None
    insertions: int = 0
    deletions: int = 0
    saw_paths: bool = False

    def freeze(self) -> PatchFileDelta:
        if not self.saw_paths or (self.old_path is None and self.new_path is None):
            raise PatchBindingError("repair patch section names no file")
        return PatchFileDelta(self.old_path, self.new_path, self.insertions, self.deletions)


def parse_patch_changes(patch_bytes: bytes) -> tuple[PatchFileDelta, ...]:
    """Return every changed file and its hunk-body line deltas, or fail closed."""
    try:
        text = patch_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PatchBindingError("repair patch is not valid UTF-8") from error
    changes: list[PatchFileDelta] = []
    section: _Section | None = None
    in_hunk = False
    for raw_line in text.splitlines():
        line = raw_line.removesuffix("\r")
        if in_hunk and not _leaves_hunk(line):
            _count_hunk_line(_require(section), line)
            continue
        section, in_hunk = _read_structure(line, section, changes)
    if section is not None:
        changes.append(section.freeze())
    if not changes:
        raise PatchBindingError("repair patch declares no file change")
    return tuple(changes)


def _leaves_hunk(line: str) -> bool:
    """Inside a hunk only these markers are structure; everything else is content.

    This is what makes an inserted line reading ``+++ x`` count as content while
    the real ``+++ b/x`` header never does — position decides, not appearance.
    """
    return line.startswith(("@@", "diff --"))


def _require(section: _Section | None) -> _Section:
    if section is None:
        raise PatchBindingError("repair patch has a hunk before any file header")
    return section


def _count_hunk_line(section: _Section, line: str) -> None:
    if line.startswith("\\"):
        return
    if line.startswith("+"):
        section.insertions += 1
    elif line.startswith("-"):
        section.deletions += 1


def _read_structure(
    line: str,
    section: _Section | None,
    changes: list[PatchFileDelta],
) -> tuple[_Section | None, bool]:
    if line.startswith(_COMBINED) or line.startswith("@@@"):
        raise PatchBindingError("repair patch uses an unsupported combined diff")
    if line.startswith("GIT binary patch"):
        raise PatchBindingError("repair patch has a binary body and cannot be summarised")
    if line.startswith("diff --git "):
        if section is not None:
            changes.append(section.freeze())
        return _started(line), False
    if section is None:
        return None, False
    if line.startswith("@@"):
        return section, True
    _read_paths(line, section)
    return section, False


def _started(line: str) -> _Section:
    section = _Section()
    matched = _GIT_HEADER.match(line)
    if matched is not None:
        section.old_path = _header_path(matched.group(1))
        section.new_path = _header_path(matched.group(2))
        section.saw_paths = True
    return section


def _read_paths(line: str, section: _Section) -> None:
    if line.startswith("--- "):
        section.old_path = _side_path(line[4:], "a/")
        section.saw_paths = True
    elif line.startswith("+++ "):
        section.new_path = _side_path(line[4:], "b/")
        section.saw_paths = True
    elif line.startswith("rename from "):
        section.old_path = _header_path(line[len("rename from ") :])
        section.saw_paths = True
    elif line.startswith("rename to "):
        section.new_path = _header_path(line[len("rename to ") :])
        section.saw_paths = True


def _side_path(raw: str, prefix: str) -> str | None:
    candidate = _unquote(raw.split("\t", 1)[0].rstrip())
    if candidate == _DEV_NULL:
        return None
    if not candidate.startswith(prefix):
        raise PatchBindingError("repair patch file header is not repository relative")
    return _validated(candidate[len(prefix) :])


def _header_path(raw: str) -> str:
    return _validated(_unquote(raw.split("\t", 1)[0].rstrip()))


def _validated(candidate: str) -> str:
    assert_repo_relative(candidate)
    return candidate


def _unquote(token: str) -> str:
    """Decode git's C-quoted path form.

    ``core.quotePath`` defaults to true, so every non-ASCII path in real ``git
    diff`` output arrives as ``"a/docs/\\352\\270\\260.md"``. This repository is
    full of Korean file names; without this a repair touching one is refused and
    can never be approved.
    """
    if not token.startswith('"'):
        return token
    if len(token) < 2 or not token.endswith('"'):
        raise PatchBindingError("repair patch path quoting is malformed")
    body, decoded, index = token[1:-1], bytearray(), 0
    while index < len(body):
        character = body[index]
        if character != "\\":
            decoded.extend(character.encode("utf-8"))
            index += 1
            continue
        escape = body[index + 1 : index + 2]
        if escape in _OCTAL_DIGITS:
            decoded.append(_octal(body[index + 1 : index + 4]))
            index += 4
            continue
        if escape not in _ESCAPES:
            raise PatchBindingError("repair patch path uses an unsupported escape")
        decoded.extend(_ESCAPES[escape].encode("utf-8"))
        index += 2
    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PatchBindingError("repair patch path is not valid UTF-8") from error


def _octal(digits: str) -> int:
    try:
        value = int(digits, 8)
    except ValueError as error:
        raise PatchBindingError("repair patch path has a malformed octal escape") from error
    if len(digits) != 3 or not 0 <= value <= 0xFF:
        raise PatchBindingError("repair patch path has a malformed octal escape")
    return value
