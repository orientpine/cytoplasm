"""Make ``automation.hermes_compat`` importable inside the vendored gateway.

Deployed FLAT to ``~/.hermes/hermes-compat/hermes_compat_boot.py`` and imported
top-level by the injected patch code (T4 run.py / T5 adapter.py). The gateway
injects ``PYTHONPATH=~/.hermes/interop_runtime`` whose ``automation`` is a
regular package; once the interop plugin imports it, that binding shadows our
runtime so ``automation.hermes_compat`` is invisible and the injected
``from automation.hermes_compat.X import ...`` fails. This merges our runtime's
``automation`` dir into the already-bound package ``__path__`` (idempotent),
exactly like the deployed ``automation.skill_generation`` plugin fix.

The pure modules live (in the repo) under ``automation/hermes_compat/`` and
import each other via clean absolute paths; deploying them as a package to
``~/.hermes/hermes-compat/automation/hermes_compat/`` plus this bootstrap makes
those absolute imports resolve in the gateway without touching interop_runtime.
"""

from __future__ import annotations

import os
import sys
from typing import cast


def ensure_importable() -> None:
    """Insert the runtime dir on sys.path and merge our automation package dir."""
    runtime = os.path.expanduser("~/.hermes/hermes-compat")
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    try:
        import automation as _automation  # force-bind even if not yet imported
    except ImportError:
        return
    raw_path = getattr(_automation, "__path__", None)
    if not isinstance(raw_path, list):
        return
    package_path = cast("list[str]", raw_path)
    automation_dir = os.path.join(runtime, "automation")
    if automation_dir not in package_path:
        package_path.append(automation_dir)


ensure_importable()
