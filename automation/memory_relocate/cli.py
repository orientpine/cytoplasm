from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, NoReturn, final, override

from automation.memory_curator import _bootstrap  # noqa: F401
from automation.memory_curator.binding import entry_digest
from automation.obsidian_write.config import ObsidianWriteError

from .model import RelocationError, RelocationRecord, RelocationState, record_key
from .propose import build_proposed_record
from .store import load_state, save_state

del _bootstrap


_DEFAULT_STATE_PATH: Final = "~/.hermes/memory-relocate/relocations.json"
_BINDING_KIND: Final = "obsidian-write"
_BINDING_SURFACE: Final = "owner-dm"
_BINDING_POLICY_VERSION: Final = 6


class _CliError(ValueError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        raise _CliError(message)


@final
class _Arguments(argparse.Namespace):
    command: str = ""
    state_path: str = _DEFAULT_STATE_PATH
    entry_file: str = ""
    channel_id: str = ""
    dry_run: bool = False


def _parse_args(argv: list[str] | None) -> _Arguments:
    parser = _ArgumentParser(prog="memory_relocate")
    commands = parser.add_subparsers(dest="command", required=True)

    propose = commands.add_parser("propose")
    _ = propose.add_argument("--entry-file", required=True)
    _ = propose.add_argument("--state-path", default=_DEFAULT_STATE_PATH)
    _ = propose.add_argument("--channel-id", default="")
    _ = propose.add_argument("--dry-run", action="store_true")

    state = commands.add_parser("state")
    state_commands = state.add_subparsers(dest="state_command", required=True)
    show = state_commands.add_parser("show")
    _ = show.add_argument("--state-path", default=_DEFAULT_STATE_PATH)

    arguments = _Arguments()
    parsed = parser.parse_args(list(sys.argv[1:] if argv is None else argv), arguments)
    return parsed


def _read_entry(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise _CliError(f"cannot read entry file: {path}") from error


def _record_summary(record: RelocationRecord) -> dict[str, str | int]:
    return {
        "digest8": record.entry_sha256[:8],
        "note_relpath": record.note_relpath,
        "reclaimable_chars": record.reclaimable_chars,
        "status": record.status,
    }


def _state_summary(state: RelocationState) -> dict[str, object]:
    counts: dict[str, int] = {}
    records: list[dict[str, str | int]] = []
    for key in sorted(state.relocations):
        record = state.relocations[key]
        counts[record.status] = counts.get(record.status, 0) + 1
        records.append(_record_summary(record))
    return {"relocations": records, "status_counts": counts}


def _print_json(payload: Mapping[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _show_state(arguments: _Arguments) -> int:
    state_path = Path(arguments.state_path).expanduser()
    _print_json(_state_summary(load_state(state_path)))
    return 0


def _propose(arguments: _Arguments) -> int:
    entry_path = Path(arguments.entry_file).expanduser()
    entry_text = _read_entry(entry_path)
    record = build_proposed_record(
        entry_text,
        source_kind="memory",
        entry_sha256=entry_digest("memory", entry_text),
        reclaimable_chars=len(entry_text),
        binding_kind=_BINDING_KIND,
        binding_surface=_BINDING_SURFACE,
        binding_channel_id=arguments.channel_id,
        binding_policy_version=_BINDING_POLICY_VERSION,
        now=datetime.now(UTC),
    )
    if not arguments.dry_run:
        state_path = Path(arguments.state_path).expanduser()
        state = load_state(state_path)
        key = record_key(record.source_kind, record.entry_sha256)
        if key in state.relocations:
            raise _CliError("a relocation record already exists for this entry")
        records = dict(state.relocations)
        records[key] = record
        save_state(state_path, RelocationState(version=state.version, relocations=records))

    summary: dict[str, str | int | bool] = {
        **_record_summary(record),
        "dry_run": arguments.dry_run,
    }
    _print_json(summary)
    return 0


def _refuse(message: str) -> int:
    print(f"MEMORY-RELOCATE-REFUSED: {message}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parse_args(argv)
        match arguments.command:
            case "propose":
                return _propose(arguments)
            case "state":
                return _show_state(arguments)
            case _:
                return _refuse("unknown command")
    except (ObsidianWriteError, RelocationError, _CliError) as error:
        return _refuse(str(error))


if __name__ == "__main__":
    sys.exit(main())
