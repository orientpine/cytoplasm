"""Owner-run shadow diagnostic for the memory curator.

Read-only by construction: it loads MEMORY.md / USER.md, classifies the
entries, and prints the ``memory-curator-shadow-v1`` report to stdout.  It
never writes a memory file, never touches curator state, never proposes a
promotion, and never opens a Discord surface — the classifier can be
compared against the legacy cue matcher before anything is trusted.

``--offline`` skips the LLM entirely (pre-LLM vetoes only), which makes the
command runnable on a node with no LiteLLM credential.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from . import _bootstrap  # noqa: F401  (side effect: puts the repo root on sys.path)
from automation.rag_ingest.sensitivity import (
    SensitivityRule,
    SensitivityRulesError,
    load_rules,
)
from automation.twin_distill.llm import LiteLlmClient, LlmConfigurationError

from .classify import classify_entries
from .classify_model import EntryVerdict
from .classify_veto import pre_llm_veto
from .curator import curate
from .model import MemoryEntry, MemoryFile, MemoryKind
from .shadow import build_shadow_report
from .watch_steps import read_native

_DEFAULT_DIR: Final = "~/.hermes/memories"
_KINDS: Final[tuple[MemoryKind, ...]] = ("memory", "user")
_RULES_RELATIVE: Final = "configs/sensitivity-rules.yaml"
_REFUSAL: Final = "MEMORY-SHADOW-REFUSED"
_OFFLINE_REASON: Final = "offline: no LLM"

EntriesByKind = Mapping[MemoryKind, tuple[MemoryEntry, ...]]


class _ShadowArgs(argparse.Namespace):
    memory_dir: str = _DEFAULT_DIR
    kind: str = "both"
    full: bool = False
    offline: bool = False
    limit: int = 0


def _parse_args(argv: list[str] | None) -> _ShadowArgs:
    parser = argparse.ArgumentParser(
        prog="memory_curator shadow",
        description="Read-only classifier diagnostic over the native memory files.",
    )
    _ = parser.add_argument(
        "--memory-dir",
        default=os.environ.get("MEMORY_CURATOR_DIR", _DEFAULT_DIR),
    )
    _ = parser.add_argument("--kind", choices=("memory", "user", "both"), default="both")
    _ = parser.add_argument("--full", action="store_true")
    _ = parser.add_argument("--offline", action="store_true")
    _ = parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args(list(sys.argv[1:] if argv is None else argv), _ShadowArgs())


def _refuse(reason: str) -> int:
    print(f"{_REFUSAL}: {reason}", file=sys.stderr)
    return 2


def _selected_kinds(kind: str) -> tuple[MemoryKind, ...]:
    match kind:
        case "memory":
            return ("memory",)
        case "user":
            return ("user",)
        case "both":
            return _KINDS
        case unreachable:
            message = f"unknown kind: {unreachable!r}"
            raise ValueError(message)


def _repo_root() -> Path:
    if _bootstrap.REPO_ROOT is not None:
        return _bootstrap.REPO_ROOT
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[2]


def _sensitivity_rules() -> tuple[SensitivityRule, ...]:
    """Load the deterministic rules, degrading to none when the seed is absent."""
    try:
        return load_rules(_repo_root() / _RULES_RELATIVE)
    except (OSError, SensitivityRulesError):
        return ()


def _limited(entries_by_kind: EntriesByKind, limit: int) -> EntriesByKind:
    if limit <= 0:
        return entries_by_kind
    remaining = limit
    limited: dict[MemoryKind, tuple[MemoryEntry, ...]] = {}
    for kind in _KINDS:
        head = entries_by_kind.get(kind, ())[:remaining]
        limited[kind] = head
        remaining -= len(head)
    return limited


def _offline_verdicts(
    entries_by_kind: EntriesByKind,
    rules: Sequence[SensitivityRule],
) -> tuple[EntryVerdict, ...]:
    """Classify with pre-LLM vetoes only; everything else stays UNCERTAIN."""
    return tuple(
        pre_llm_veto(entry.text, source_kind=kind, rules=rules)
        or EntryVerdict(
            source_kind=kind,
            entry_text=entry.text,
            route="UNCERTAIN",
            evidence="",
            reason=_OFFLINE_REASON,
            veto=None,
            llm_called=False,
        )
        for kind in _KINDS
        for entry in entries_by_kind.get(kind, ())
    )


def _online_verdicts(
    entries_by_kind: EntriesByKind,
    rules: Sequence[SensitivityRule],
) -> tuple[EntryVerdict, ...] | None:
    try:
        client = LiteLlmClient.from_environment(os.environ)
    except LlmConfigurationError:
        return None
    return classify_entries(entries_by_kind, client=client, rules=rules)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    memory_dir = Path(args.memory_dir).expanduser()
    if not memory_dir.is_dir():
        return _refuse(f"메모리 디렉터리를 찾을 수 없음: {memory_dir}")

    files: dict[MemoryKind, MemoryFile] = {
        kind: read_native(memory_dir, kind)[1] for kind in _KINDS
    }
    cue_matched: dict[MemoryKind, frozenset[str]] = {
        kind: frozenset(entry.text for entry in curate(files[kind]).promotion_candidates)
        for kind in _KINDS
    }
    entries_by_kind = _limited(
        {kind: files[kind].entries for kind in _selected_kinds(args.kind)},
        args.limit,
    )
    rules = _sensitivity_rules()

    if args.offline:
        verdicts = _offline_verdicts(entries_by_kind, rules)
    else:
        online = _online_verdicts(entries_by_kind, rules)
        if online is None:
            return _refuse("LITELLM_AGENT_KEY 필요 (또는 --offline 사용)")
        verdicts = online

    report = build_shadow_report(files, verdicts, cue_matched=cue_matched, full=args.full)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
