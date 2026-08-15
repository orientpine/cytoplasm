"""Strict YAML boundary for research-group rosters."""

from __future__ import annotations

import os
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol, TypeAlias, runtime_checkable

import yaml

from .schema import Roster
from .validator import RosterError, YamlValue, validate_roster

ROSTER_ENV: Final = "AUTOPHAGY_ROSTER"
DEFAULT_ROSTER_PATH: Final = Path("~/.hermes/roster.yaml")


class _ComposedNode(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def tag(self) -> str: ...

    @property
    def value(
        self,
    ) -> str | list["_ComposedNode"] | list[tuple["_ComposedNode", "_ComposedNode"]]: ...


class _SafeLoader(Protocol):
    pass


@runtime_checkable
class _YamlApi(Protocol):
    SafeLoader: type[_SafeLoader]

    def compose(
        self,
        stream: str,
        loader: type[_SafeLoader],
    ) -> _ComposedNode | None: ...

    def safe_load(self, stream: str) -> YamlValue: ...


def _typed_yaml_api() -> _YamlApi:
    if not isinstance(yaml, _YamlApi):
        message = "PyYAML does not expose the required safe parser API"
        raise ImportError(message)
    return yaml


_YAML: Final = _typed_yaml_api()


class _NodeKind(StrEnum):
    SCALAR = "scalar"
    SEQUENCE = "sequence"
    MAPPING = "mapping"


_NODE_KINDS: Final = {kind.value: kind for kind in _NodeKind}
_YAML_MERGE_TAG: Final = "tag:yaml.org,2002:merge"


def _node_kind(node: _ComposedNode) -> _NodeKind:
    try:
        return _NODE_KINDS[node.id]
    except KeyError as error:
        raise RosterError(f"unsupported YAML node type: {node.id}") from error


def _accept_scalar(node: _ComposedNode) -> None:
    del node


def _reject_sequence_duplicates(node: _ComposedNode) -> None:
    children = node.value
    if not isinstance(children, list):
        raise RosterError("invalid YAML sequence node")
    for child in children:
        if isinstance(child, tuple):
            raise RosterError("invalid YAML sequence entry")
        _reject_duplicate_keys(child)


def _reject_mapping_duplicates(node: _ComposedNode) -> None:
    pairs = node.value
    if not isinstance(pairs, list):
        raise RosterError("invalid YAML mapping node")
    seen: set[tuple[str, str]] = set()
    for pair in pairs:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise RosterError("invalid YAML mapping entry")
        key, value = pair
        if key.id != "scalar" or not isinstance(key.value, str):
            raise RosterError("YAML mapping keys must be scalar values")
        if key.tag == _YAML_MERGE_TAG:
            raise RosterError("YAML merge keys are not supported")
        identity = (key.tag, key.value)
        if identity in seen:
            raise RosterError(f"duplicate YAML key: {key.value}")
        seen.add(identity)
        _reject_duplicate_keys(value)


_NodeHandler: TypeAlias = Callable[[_ComposedNode], None]
_NODE_HANDLERS: Final[dict[_NodeKind, _NodeHandler]] = {
    _NodeKind.SCALAR: _accept_scalar,
    _NodeKind.SEQUENCE: _reject_sequence_duplicates,
    _NodeKind.MAPPING: _reject_mapping_duplicates,
}


def _reject_duplicate_keys(node: _ComposedNode) -> None:
    _NODE_HANDLERS[_node_kind(node)](node)


def parse_roster(text: str, *, source: str = "<string>") -> Roster:
    """Parse roster YAML text and reject malformed or ambiguous input."""
    try:
        composed = _YAML.compose(text, _YAML.SafeLoader)
        if composed is not None:
            _reject_duplicate_keys(composed)
        raw = _YAML.safe_load(text)
    except (yaml.YAMLError, RecursionError) as error:
        raise RosterError(f"{source}: roster is not valid YAML: {error}") from error
    except RosterError as error:
        raise RosterError(f"{source}: {error}") from error
    try:
        return validate_roster(raw)
    except RosterError as error:
        raise RosterError(f"{source}: {error}") from error


def load_roster(path: Path) -> Roster:
    """Read and parse one roster file."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise RosterError(f"cannot read roster file {path}: {error}") from error
    return parse_roster(text, source=str(path))


def roster_path() -> Path:
    """Resolve the group roster path from the environment or the runtime default."""
    raw = os.environ.get(ROSTER_ENV, "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_ROSTER_PATH.expanduser()
