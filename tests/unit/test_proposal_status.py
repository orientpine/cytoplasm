from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from skills.proposal.scripts import proposal_version  # noqa: E402


def _run(root: Path, slug: str) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "PROPOSAL_ROOT": str(root)}
    return subprocess.run(
        [sys.executable, "skills/proposal/scripts/proposal_cli.py", "status", "--slug", slug, "--json"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_status_reports_published_head(tmp_path: Path) -> None:
    store = proposal_version.VersionStore(tmp_path)
    staging = store.begin("demo", "a" * 64)
    assert isinstance(staging, proposal_version.Staging)
    version = store.promote("demo", staging, {"parent": None, "schema_version": 1})
    (tmp_path / "demo" / "versions" / version / "publish-receipt.json").write_text("{}\n")

    result = _run(tmp_path, "demo")

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"slug": "demo", "state": "published", "version": "v000001"}


def test_status_without_receipt_is_staged(tmp_path: Path) -> None:
    store = proposal_version.VersionStore(tmp_path)
    staging = store.begin("demo", "b" * 64)
    assert isinstance(staging, proposal_version.Staging)
    store.promote("demo", staging, {"parent": None, "schema_version": 1})

    result = _run(tmp_path, "demo")

    assert result.returncode == 0
    assert json.loads(result.stdout)["state"] == "staged"


def test_status_requires_existing_slug(tmp_path: Path) -> None:
    result = _run(tmp_path, "missing")

    assert result.returncode != 0
    assert "proposal slug is missing" in result.stderr
