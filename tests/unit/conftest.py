"""Redirect unit-test HOME and the ``gws`` binary before collection.

Both run at import because production modules resolve ``Path.home()`` and
``shutil.which("gws")`` during their own imports.

``gws`` on this PATH carries the owner's Google credentials in the OS keyring, so
the HOME redirect never revoked them — on 2026-09-11 the suite was measured
inserting real tasks into the owner's list. All four skills and ``DriveClient``
resolve the binary through ``shutil.which``, so PATH is the one choke point that
also covers child processes; ``test_gws_path_guard.py`` pins it.
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

_GWS_DIR = Path(tempfile.mkdtemp(prefix="autophagy-unit-gws-"))
_GWS_GUARD = _GWS_DIR / "gws"
_gws_log_override = os.environ.get("AUTOPHAGY_UNIT_GWS_GUARD_LOG", "")
_GWS_GUARD_LOG = Path(_gws_log_override) if _gws_log_override else _GWS_DIR / "calls.log"
_ = _GWS_GUARD.write_text(
    f"""#!/bin/sh
printf '%s\\t%s\\n' "${{PYTEST_CURRENT_TEST:-<no-test>}}" "$*" >> '{_GWS_GUARD_LOG}'
echo 'UNIT-TEST-GWS-BLOCKED: a unit test tried to run the real gws binary.' >&2
echo 'Inject a runner (FakeGws) or set the skill *_GWS_BIN override.' >&2
exit 97
""",
    encoding="utf-8",
)
_GWS_GUARD.chmod(0o755)
os.environ["AUTOPHAGY_UNIT_GWS_GUARD"] = str(_GWS_GUARD)
os.environ["AUTOPHAGY_UNIT_GWS_GUARD_LOG"] = str(_GWS_GUARD_LOG)
os.environ["PATH"] = f"{_GWS_DIR}{os.pathsep}{os.environ.get('PATH', '')}"

_ = atexit.register(shutil.rmtree, _GWS_DIR, ignore_errors=True)
