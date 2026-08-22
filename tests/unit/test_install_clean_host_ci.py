"""CI must prove the installer entrypoint still runs on a bare interpreter.

`python3 -m automation.install --dry-run` is the very first command a third
party runs, before any package-manager step exists in the procedure. Whatever it
imports at module scope therefore becomes an undocumented prerequisite, and it
has broken for real once: after `dcddaa21` a module-scope roster import made a
plain dry-run die with a bare `ModuleNotFoundError` traceback.

`tests/unit/test_install_third_party_boundary.py` only simulates that by
deleting entries from `sys.modules`, which cannot catch a dependency the
developer machine happens to have installed. The honest check is a container
with nothing in it, so this module pins the CI job that provides one.

The assertions are on the *contract* — a slim image, no dependency install
before the run, `--dry-run`, and the boundary unit tests kept alongside — not on
YAML formatting.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

_WORKFLOW: Final = Path(".github/workflows/ci.yml")
_JOB: Final = "clean-host-install"


def _workflow() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


def _job_block() -> str:
    text = _workflow()
    start = text.index(f"  {_JOB}:")
    following = re.compile(r"^  [a-z][a-z0-9-]*:$", re.MULTILINE)
    match = following.search(text, start + len(f"  {_JOB}:"))
    return text[start : match.start()] if match else text[start:]


def test_ci_has_a_clean_host_installer_job() -> None:
    assert f"  {_JOB}:" in _workflow()


def test_the_job_runs_in_a_bare_python_container() -> None:
    # Given — a GitHub runner image already carries far more than a fresh node
    block = _job_block()

    assert "container:" in block
    assert "python:3.12-slim" in block


def test_the_job_runs_the_installer_dry_run() -> None:
    block = _job_block()

    assert "automation.install" in block
    assert "--dry-run" in block
    assert "--update-trust-key" in block


def test_the_job_installs_no_dependencies_before_the_dry_run() -> None:
    # Given — installing anything first would hide the exact regression this
    # job exists to catch (an import surface that widened past the stdlib).
    block = _job_block()
    dry_run_at = block.index("--dry-run")

    assert "pip install" not in block[:dry_run_at]
    assert "requirements-dev.txt" not in block[:dry_run_at]


def test_the_existing_verify_job_is_untouched() -> None:
    # Given — the new job is additive; lint and the unit suite still gate merges
    text = _workflow()

    assert "  verify:" in text
    assert "ruff check . --exclude skills/mail/vendor" in text
    assert "python -m pytest tests/unit -q" in text


def test_the_workflow_stays_read_only_and_off_the_node() -> None:
    text = _workflow()

    assert "contents: read" in text
    assert "ssh" not in text.lower().replace("ssh-ed25519", "")
