"""Find ANSI-C `$'…'` and locale `$"…"` quoting in one shell word (RCB todo 58, verifier B58-R4-1).

Token comparison strips ordinary quotes, so `$'bash'` or `$'\\142ash'` would hide a shell name. The
source-order policy rejects these forms instead of decoding them. Only real openings count: a `$'`
inside `'…'` or `"…"` is literal text, while `${…}` is walked in any context because bash expands it
there. `$(…)` bodies are skipped here because the policy scans them as their own text.
"""
from __future__ import annotations


def special_quotes(word: str) -> set[str]:
    found: set[str] = set()
    _ = _unquoted(word, 0, "", found)
    return found


def _unquoted(word: str, i: int, closer: str, found: set[str]) -> int:
    while i < len(word):
        char = word[i]
        if closer and char == closer:
            return i + 1
        if char == "\\":
            i += 2
        elif word.startswith("$'", i):
            found.add("ansi-c")
            i = _ansi_end(word, i + 2)
        elif word.startswith('$"', i):
            found.add("locale")
            i = _double_end(word, i + 2, found)
        elif char == "'":
            end = word.find("'", i + 1)
            i = len(word) if end < 0 else end + 1
        elif char == '"':
            i = _double_end(word, i + 1, found)
        elif word.startswith("${", i):
            i = _unquoted(word, i + 2, "}", found)
        elif word.startswith(("$(", "<(", ">("), i):
            i = _paren_end(word, i + 2)
        elif char == "`":
            end = word.find("`", i + 1)
            i = len(word) if end < 0 else end + 1
        else:
            i += 1
    return i


def _ansi_end(word: str, i: int) -> int:
    while i < len(word) and word[i] != "'":
        i += 2 if word[i] == "\\" else 1
    return i + 1


def _double_end(word: str, i: int, found: set[str]) -> int:
    while i < len(word):
        char = word[i]
        if char == "\\":
            i += 2
        elif char == '"':
            return i + 1
        elif word.startswith("${", i):
            i = _unquoted(word, i + 2, "}", found)
        elif word.startswith("$(", i):
            i = _paren_end(word, i + 2)
        else:
            i += 1
    return i


def _paren_end(word: str, i: int) -> int:
    depth = 1
    while i < len(word):
        char = word[i]
        if char == "\\":
            i += 2
            continue
        if char in "'\"":
            end = word.find(char, i + 1)
            i = len(word) if end < 0 else end + 1
            continue
        depth += {"(": 1, ")": -1}.get(char, 0)
        i += 1
        if depth == 0:
            return i
    return i
