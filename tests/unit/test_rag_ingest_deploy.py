"""The RAG package deployer must preserve provenance, locking, and read-back."""
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_DEPLOY = _REPO / "automation" / "rag_ingest" / "deploy.sh"


def test_rag_ingest_deployer_has_the_production_safety_contract() -> None:
    text = _DEPLOY.read_text(encoding="utf-8")

    assert "deploy_provenance_check" in text
    assert "|| exit 4" in text
    assert "tar -C" in text
    assert "--exclude='rag_ingest/cron'" in text
    assert "__pycache__" in text
    assert "flock -w 300" in text
    assert ".hermes/rag-ingest/watch.lock" in text
    assert ".hermes/rag_ingest_runtime" in text
    assert ".hermes/scripts/rag_ingest_watch.py" in text
    assert "sha256sum" in text
    assert "expected_count" in text
    assert 'hermes cron list --all' in text
    assert 'hermes cron create "every 10m" --name rag-ingest-watch' in text


def test_remote_payloads_run_from_a_readable_directory() -> None:
    """`sudo -u <acct>` inherits the CALLER's cwd (~oriclaw, unreadable to the target).

    `find` then fails with "Failed to restore initial working directory" and exits
    non-zero, so `set -euo pipefail` aborted the deploy right after the tar extract —
    before the read-back and before the watcher wrapper was pushed. Measured in
    production 2026-08-22: the runtime landed, the wrapper did not, and every ingest
    tick stayed broken while the deployer looked like it had run.

    Scans EVERY shipped script rather than a named pair: the first version of this
    regression hardcoded two files and therefore did not see the RAG-stack deployer
    reintroduce the identical payload one PR later. Only REMOTE payloads are
    constrained; a local find runs in a directory its own caller can read.
    """
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[2]
    markers = ("run_agent ", "run_ops ", "sudo -n -u")
    checked = 0
    for script in sorted((root / "automation").rglob("*.sh")):
        for line in script.read_text(encoding="utf-8").splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#") or "find " not in line:
                continue
            if not any(marker in line for marker in markers):
                continue
            checked += 1
            assert "cd " in line.split("find ")[0], (
                f"{script.relative_to(root)}: remote payload runs find without first "
                f"entering a readable directory: {stripped[:120]}"
            )
    assert checked >= 3, f"expected to inspect every remote payload, saw {checked}"
