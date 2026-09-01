"""Pin the RAG wrapper row separately because FS3 pins the manifest test file."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_HEALTHCHECK = _REPO / "automation" / "healthcheck.sh"


def _live_checks() -> tuple[str, str, list[str]]:
    script = 'source "{}"; printf "%s\\0" "$RAG_NODE" "$NODE_OPS_ACCOUNT" "${{LIVE_CHECKS[@]}}"'.format(_HEALTHCHECK)
    result = subprocess.run(
        ("bash", "-c", script),
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "HEALTHCHECK_SSH_USER": "",
            "HEALTHCHECK_SSH_IDENTITY": "",
        },
    )

    assert result.returncode == 0, result.stderr.decode()
    rag_node, ops_account, *checks, _ = result.stdout.decode().split("\0")
    return rag_node, ops_account, checks


def test_healthcheck_registers_a_rag_wrapper_drift_probe_with_rag_transport() -> None:
    rag_node, ops_account, checks = _live_checks()

    expected = (
        f"{rag_node} healthcheck probe allowlist matches the checks|"
        f"healthcheck_wrapper_current|{rag_node}|{ops_account}|"
        "automation/healthcheck_probe_wrapper.sh"
    )
    assert checks.count(expected) == 1
