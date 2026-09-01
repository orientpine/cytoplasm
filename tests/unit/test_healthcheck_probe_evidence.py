"""RAG healthcheck probe evidence is private and cannot alter a probe verdict."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_EVIDENCE_HELPER = _REPO / "automation" / "healthcheck_probe_evidence.sh"


def _run_probe(*, probe_return_code: int, evidence_dir: Path) -> subprocess.CompletedProcess[str]:
    """Run a synthetic probe through the production evidence wrapper, without SSH."""
    script = "\n".join((
        'source "$1"',
        "run_check() { return \"$PROBE_RETURN_CODE\"; }",
        'healthcheck_run_check_with_evidence "rag embedding|embedding_health|rag|ops|http://127.0.0.1:8001/health"',
    ))
    return subprocess.run(
        ("bash", "-c", script, "bash", str(_EVIDENCE_HELPER)),
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO,
        env={
            **os.environ,
            "HEALTHCHECK_EVIDENCE_DIR": str(evidence_dir),
            "PROBE_RETURN_CODE": str(probe_return_code),
        },
    )


def test_healthcheck_wires_the_evidence_wrapper() -> None:
    healthcheck = (_REPO / "automation" / "healthcheck.sh").read_text(encoding="utf-8")

    assert 'source "$(dirname "${BASH_SOURCE[0]}")/healthcheck_probe_evidence.sh"' in healthcheck
    assert 'if healthcheck_run_check_with_evidence "$definition"; then' in healthcheck


@pytest.mark.parametrize(
    ("probe_return_code", "failure_class"),
    ((0, "none"), (255, "transport"), (1, "service")),
)
def test_rag_probes_record_transport_or_service_evidence(
    tmp_path: Path, probe_return_code: int, failure_class: str
) -> None:
    result = _run_probe(probe_return_code=probe_return_code, evidence_dir=tmp_path / "private")

    assert result.returncode == probe_return_code
    raw = (tmp_path / "private" / "probe-evidence.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in raw.splitlines()]
    assert rows[0]["probe"] == "rag embedding"
    assert rows[0]["rc"] == probe_return_code
    assert rows[0]["failure_class"] == failure_class
    assert isinstance(rows[0]["elapsed_ms"], int)
    assert rows[0]["elapsed_ms"] >= 0
    assert "127.0.0.1" not in raw


@pytest.mark.parametrize("probe_return_code", (0, 1))
def test_evidence_write_failure_never_changes_a_probe_verdict_or_exit_code(
    tmp_path: Path, probe_return_code: int
) -> None:
    blocked = tmp_path / "not-a-directory"
    _ = blocked.write_text("not a directory", encoding="utf-8")

    result = _run_probe(probe_return_code=probe_return_code, evidence_dir=blocked)

    assert result.returncode == probe_return_code
    assert blocked.read_text(encoding="utf-8") == "not a directory"
