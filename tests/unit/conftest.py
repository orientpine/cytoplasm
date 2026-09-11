"""Redirect unit-test home state before collection.

Unit tests used to write into the developer's ``~/.hermes``. This runs at import
because production modules can resolve ``Path.home()`` during their own imports.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

_UNIT_HOME = Path(tempfile.mkdtemp(prefix="autophagy-unit-home-"))
os.environ["HOME"] = str(_UNIT_HOME)
for _xdg_home_var in (
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
):
    _ = os.environ.pop(_xdg_home_var, None)

_ = atexit.register(shutil.rmtree, _UNIT_HOME, ignore_errors=True)
