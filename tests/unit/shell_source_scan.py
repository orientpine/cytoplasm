"""Closed-world bash lexer (RCB todo 58): every word with its file offset and subshell flag, the
commands they form, and every locally executed substitution and heredoc body; raises on syntax it
cannot follow. Policy lives in `test_deploy_provenance_source_order.py`."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

_KEEP_POSITION: Final = frozenset({"then", "do", "else", "elif", "if", "while", "until", "!", "time"})
_ASSIGNMENT: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?\+?=")
SOURCE_WORDS: Final = frozenset({"source", "."})
Mode = Literal["none", "body", "inner"]
Token = tuple[str, str, int, bool]


@dataclass(frozen=True, slots=True)
class Word:
    pos: int
    text: str
    in_subshell: bool


@dataclass(frozen=True, slots=True)
class Command:
    index: int
    word: Word
    args: tuple[Word, ...]
    in_function: bool


@dataclass(frozen=True, slots=True)
class Scan:
    commands: tuple[Command, ...]
    words: tuple[Word, ...]
    substitutions: tuple[tuple[int, str], ...]
    heredocs: tuple[tuple[int, str], ...]


def plain(raw: str) -> str:
    return re.sub(r"[\"'\\]", "", raw)


class _Lexer:
    """`subs` collects (file offset, body) of every command substitution that runs in THIS shell."""

    def __init__(self, text: str, base: int = 0) -> None:
        self.text, self.base = text, base
        self.subs, self.heredocs = list[tuple[int, str]](), list[tuple[int, str]]()

    def quoted(self, i: int, quote: str, record: bool) -> int:
        text = self.text
        while i < len(text):
            char = text[i]
            if char == "\\" and quote != "'":
                i += 2
            elif char == quote:
                return i + 1
            elif quote == '"' and text.startswith(("$(", "${"), i):
                i = self.group(i + 2, text[i + 1], ("body" if text[i + 1] == "(" else "inner") if record else "none")
            elif quote == '"' and char == "`":
                i = self.backtick(i + 1, record)
            else:
                i += 1
        raise ValueError(f"unterminated {quote}")

    def backtick(self, i: int, record: bool) -> int:
        end = i
        while end < len(self.text) and self.text[end] != "`":
            end += 2 if self.text[end] == "\\" else 1
        if end >= len(self.text):
            raise ValueError("unterminated `")
        if record:
            self.subs.append((self.base + i, self.text[i:end].replace("\\`", "`")))
        return end + 1

    def group(self, i: int, opener: str, mode: Mode) -> int:
        text, start, depth = self.text, i, 1
        closer, inner = {"(": ")", "{": "}"}[opener], mode == "inner"
        while i < len(text):
            char = text[i]
            if char == "\\":
                i += 2
            elif char in "'\"":
                i = self.quoted(i + 1, char, inner and char == '"')
            elif char == "`":
                i = self.backtick(i + 1, inner)
            elif text.startswith(("$(", "${"), i):
                i = self.group(i + 2, text[i + 1], ("body" if text[i + 1] == "(" else "inner") if inner else "none")
            elif char == opener:
                depth, i = depth + 1, i + 1
            elif char == closer:
                depth, i = depth - 1, i + 1
                if depth == 0:
                    if mode == "body":
                        self.subs.append((self.base + start, text[start:i - 1]))
                    return i
            else:
                i += 1
        raise ValueError(f"unterminated {opener}")

    def expanding(self) -> None:
        i = 0
        while i < len(self.text):
            if self.text[i] == "\\":
                i += 2
            elif self.text.startswith(("$(", "${"), i):
                i = self.group(i + 2, self.text[i + 1], "body" if self.text[i + 1] == "(" else "inner")
            elif self.text[i] == "`":
                i = self.backtick(i + 1, True)
            else:
                i += 1

    def tokens(self) -> list[Token]:
        text, out, word, pending = self.text, list[Token](), list[str](), list[tuple[str, bool, bool]]()
        i, start, depth = 0, 0, 0

        def flush() -> None:
            if word:
                out.append(("word", "".join(word), self.base + start, depth > 0))
                word.clear()

        def add(piece: str, at: int) -> None:
            nonlocal start
            if not word:
                start = at
            word.append(piece)

        while i < len(text):
            char = text[i]
            if char == "\\":
                if text[i + 1:i + 2] != "\n":
                    add(text[i:i + 2], i)
                i += 2
            elif char in " \t":
                flush()
                i += 1
            elif char == "\n":
                flush()
                out.append(("sep", "\n", self.base + i, depth > 0))
                i += 1
                for delimiter, dash, expands in pending:
                    body, first = list[str](), i
                    while i < len(text):
                        end = text.find("\n", i)
                        line = text[i:] if end < 0 else text[i:end]
                        i = len(text) if end < 0 else end + 1
                        if (line.lstrip("\t") if dash else line) == delimiter:
                            break
                        body.append(line)
                    self.heredocs.append((self.base + first, "\n".join(body)))
                    if expands:
                        nested = _Lexer("\n".join(body), self.base + first)
                        nested.expanding()
                        self.subs.extend(nested.subs)
                pending.clear()
            elif char == "#" and not word:
                end = text.find("\n", i)
                i = len(text) if end < 0 else end
            elif char in "'\"`":
                end = self.backtick(i + 1, True) if char == "`" else self.quoted(i + 1, char, char == '"')
                add(text[i:end], i)
                i = end
            elif text.startswith("$((", i) or (text.startswith("((", i) and not word):
                end = self.group(i + (2 if char == "$" else 1), "(", "inner")
                add(text[i:end], i)
                i = end
            elif text.startswith(("$(", "<(", ">(", "${"), i):
                end = self.group(i + 2, text[i + 1], "inner" if text[i + 1] == "{" else "body")
                add(text[i:end], i)
                i = end
            elif char == "(" and "".join(word).endswith("="):
                end = self.group(i + 1, "(", "inner")
                add(text[i:end], i)
                i = end
            elif text.startswith("$'", i):
                end = self.quoted(i + 2, "'", False)
                add(text[i:end], i)
                i = end
            elif text.startswith("<<", i) and not text.startswith("<<<", i):
                flush()
                i += 2
                dash = text[i:i + 1] == "-"
                i += 1 if dash else 0
                match = re.match(r"[ \t]*([^\s;&|<>()]+)", text[i:])
                if match is None:
                    raise ValueError("heredoc without a delimiter")
                raw = match.group(1)
                pending.append((plain(raw), dash, not any(q in raw for q in "'\"\\")))
                i += match.end()
            elif char in "<>":
                if "".join(word).isdigit():
                    word.clear()
                flush()
                end = i
                while end < len(text) and text[end] in "<>&":
                    end += 1
                out.append(("redirect", text[i:end], self.base + i, depth > 0))
                i = end
            elif char == "(" and re.match(r"\([ \t]*\)", text[i:]):
                flush()
                out.append(("sep", "()", self.base + i, depth > 0))
                i = text.index(")", i) + 1
            elif char in ";&|()":
                flush()
                depth += 1 if char == "(" else -1 if char == ")" and depth else 0
                out.append(("sep", char, self.base + i, depth > 0))
                i += 1
            else:
                add(char, i)
                i += 1
        flush()
        return out


def _commands(tokens: list[Token]) -> list[Command]:
    found: list[Command] = []
    position, target, function_next, test = True, False, False, False
    braces: list[bool] = []
    cases: list[str] = []
    for index, (kind, value, pos, subshell) in enumerate(tokens):
        if test:
            test = not (kind == "word" and value == "]]")
            continue
        if cases and cases[-1] == "pattern":
            if kind == "word" and value == "esac":
                _ = cases.pop()
            elif kind == "sep" and value == ")":
                cases[-1], position = "body", True
            continue
        if kind != "word":
            if kind == "sep" and value == "()":
                function_next = True
            elif kind == "sep":
                if value == ";" and cases and tokens[index - 1][:2] == ("sep", ";"):
                    cases[-1] = "pattern"
                position = True
            target = kind == "redirect"
            continue
        word = Word(pos, value, subshell)
        if target:
            target = False
        elif value in {"{", "}"} and position:
            if value == "{":
                braces.append(function_next)
                function_next = False
            elif braces:
                _ = braces.pop()
        elif position and index + 1 < len(tokens) and tokens[index + 1][:2] == ("sep", "()"):
            continue
        elif position:
            if value in _KEEP_POSITION or _ASSIGNMENT.match(value):
                continue
            if value == "function":
                function_next = True
            elif value == "esac" and cases:
                _ = cases.pop()
            elif value in {"[[", "(("} or value.startswith("(("):
                test, position = value == "[[", False
            else:
                found.append(Command(index, word, (), any(braces)))
                position = False
        elif found and found[-1].word.text == "case" and value == "in" and not found[-1].args[1:]:
            cases.append("pattern")
        elif found:
            last = found[-1]
            found[-1] = Command(last.index, last.word, (*last.args, word), last.in_function)
    return found


def scan(text: str, base: int = 0) -> Scan:
    lexer = _Lexer(text, base)
    tokens = lexer.tokens()
    words = tuple(Word(pos, value, subshell) for kind, value, pos, subshell in tokens if kind == "word")
    return Scan(tuple(_commands(tokens)), words, tuple(lexer.subs), tuple(lexer.heredocs))
