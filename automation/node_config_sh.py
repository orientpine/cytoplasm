"""Shell bridge for the typed node configuration."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from automation.node_config import load_node_config, node_config_values


def print_env() -> None:
    """Print shell-sourceable assignments for every resolved field."""
    configured = os.environ.get("HEALTHCHECK_NODE_CONFIG_PATH")
    config = load_node_config(Path(configured) if configured else None)
    for name, value in node_config_values(config).items():
        print(f"NODE_{name.upper()}={shlex.quote(value)}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="node-config")
    _ = parser.add_argument("--print-env", action="store_true", required=True)
    _ = parser.parse_args()
    print_env()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
