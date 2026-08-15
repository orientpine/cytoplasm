"""Opt-in installer components — unit sets the installer converges only when asked.

Opt-in means ABSENT by default, not disabled by default: a component nobody named
contributes no file and no timer at all, so a plan built without it is byte-for-byte the
plan that existed before the component was written. That property is what lets an
optional feature ship without changing every existing install.

The registry lives in its own module so adding the next component is a two-line change
here rather than more weight in `assets.py`, which already carries the always-on assets.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final


class UnknownComponentError(RuntimeError):
    """A requested component name is not in the registry; nothing is installed."""


@dataclass(frozen=True, slots=True)
class OptInComponent:
    """One optional unit set, rendered and enabled exactly like the always-on units."""

    name: str
    #: Directory of `$NODE_*` unit templates, relative to the repository root.
    source: Path
    units: tuple[str, ...]

    @property
    def timers(self) -> tuple[str, ...]:
        """Only timers are enabled — a `.service` is started by its timer, not directly."""
        return tuple(unit for unit in self.units if unit.endswith(".timer"))


#: The one registry. `installer.py --with-component <name>` validates against these keys.
OPT_IN_COMPONENTS: Final[Mapping[str, OptInComponent]] = {
    "managed-sync": OptInComponent(
        name="managed-sync",
        source=Path("automation/managed_sync/systemd"),
        units=(
            "autophagy-managed-sync.service",
            "autophagy-managed-sync.timer",
        ),
    ),
}


def resolve_components(names: Sequence[str]) -> tuple[OptInComponent, ...]:
    """Resolve requested component names, refusing unknown ones fail-closed.

    Silently dropping a name the operator typed would install less than they asked for
    and still report success — the one outcome an installer must never produce.
    """
    unknown = sorted(set(names) - set(OPT_IN_COMPONENTS))
    if unknown:
        known = ", ".join(sorted(OPT_IN_COMPONENTS))
        raise UnknownComponentError(
            f"unknown opt-in component(s): {', '.join(unknown)}; known: {known}"
        )
    return tuple(OPT_IN_COMPONENTS[name] for name in sorted(set(names)))
