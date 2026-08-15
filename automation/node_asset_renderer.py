"""Render tracked systemd and sudoers seeds for one node installation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from string import Template

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from automation.node_config import (
    NodeConfig,
    NodeConfigError,
    load_node_config,
    node_config_values,
)


def render_asset(source: Path, config: NodeConfig) -> str:
    """Substitute only explicit NODE_* placeholders in a tracked asset template.

    Substitution is raw text into root-owned sudoers/systemd assets, so control
    characters are rejected here as well as in ``node_config._validate``. This
    second, independent layer also covers a ``NodeConfig`` built directly rather
    than loaded through the validating parser.
    """
    values: dict[str, str] = {}
    for name, value in node_config_values(config).items():
        if not value.isprintable():
            raise NodeConfigError(
                f"node configuration field must not contain control characters: {name}"
            )
        values[f"NODE_{name.upper()}"] = value
    rendered = Template(source.read_text(encoding="utf-8")).safe_substitute(values)
    if "$NODE_" in rendered:
        raise KeyError("asset contains an unknown NODE_* placeholder")
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser(prog="node-asset-renderer")
    _ = parser.add_argument("source", type=Path)
    _ = parser.add_argument("destination", type=Path)
    _ = parser.parse_args()
    destination = Path(sys.argv[2])
    _ = destination.write_text(
        render_asset(Path(sys.argv[1]), load_node_config()), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
