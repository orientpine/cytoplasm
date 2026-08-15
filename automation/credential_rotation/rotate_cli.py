#!/usr/bin/env python3
"""Owner dashboard credential rotation entry point.

Mirrors ``automation/repair/repair_cli.py``: when this file is executed by
absolute path (the way operators and sudo invoke it) the repo root is placed on
``sys.path`` so the package resolves under its canonical
``automation.credential_rotation`` name. That keeps a single import identity for
the CLI and the unit tests instead of loading the same modules twice under two
different names.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.credential_rotation.rotate import main


if __name__ == "__main__":
    sys.exit(main())
