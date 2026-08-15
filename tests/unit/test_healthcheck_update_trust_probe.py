from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HEALTHCHECK = ROOT / "automation" / "healthcheck.sh"
PROBE = ROOT / "automation" / "update_trust_probe.sh"


def test_healthcheck_runs_update_trust_before_release_drift_probes() -> None:
    # Given: the healthcheck's ordered probe inventory.
    body = HEALTHCHECK.read_text(encoding="utf-8")

    # When: the signed-update and release checks are located.
    trust_index = body.index("|update_trust|")
    mirror_index = body.index("|checkout_mirrors_origin|")
    release_index = body.index("|release_matches_origin|")

    # Then: trust owns an unsigned-head incident before generic drift grading runs.
    assert trust_index < mirror_index < release_index


def test_update_trust_probe_when_signature_is_missing_surfaces_specific_reason(
    tmp_path: Path,
) -> None:
    # Given: the verifier reports the precise unsigned-head block category.
    fake_python = tmp_path / "python3"
    script = "\n".join(
        (
            "#!/usr/bin/env bash",
            "printf 'UNSIGNED-HEAD: origin/main lacks a trusted signed release tag\\n' >&2",
            "exit 1",
            "",
        )
    )
    _ = fake_python.write_text(script, encoding="utf-8")
    fake_python.chmod(0o755)
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    command = (
        f'source "{PROBE}"; '
        f'UPDATE_TRUST_PYTHON="{fake_python}" '
        f'UPDATE_TRUST_SCRIPT="{tmp_path / "update_trust.py"}" '
        f'probe_update_trust ignored ignored "{mirror}"'
    )

    # When: healthcheck executes its local update-trust probe.
    completed = subprocess.run(
        ("bash", "-c", command),
        env={**os.environ, "NODE_REQUIRE_SIGNED_UPDATES": "1"},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    # Then: the existing healthcheck failure stream carries the verifier category.
    assert completed.returncode == 1
    assert "UPDATE-TRUST-BLOCK UNSIGNED-HEAD" in completed.stderr
