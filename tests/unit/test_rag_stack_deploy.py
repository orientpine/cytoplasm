"""The canonical personal-RAG tree must deploy without touching node secrets."""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_DEPLOY = _REPO / "automation" / "rag_stack" / "deploy.sh"


def test_rag_stack_deployer_has_provenance_target_and_lock_contract() -> None:
    text = _DEPLOY.read_text(encoding="utf-8")

    assert 'deploy_provenance_check "$repo_root" "$repo_root/configs/rag" || exit 4' in text
    assert 'host="${RAG_STACK_SSH_HOST:-$NODE_RAG_NODE_NAME}"' in text
    assert "sudo -n -u ops -H" in text
    assert "flock -w 300" in text


def test_rag_stack_deployer_preserves_node_secret_and_selectively_syncs() -> None:
    text = _DEPLOY.read_text(encoding="utf-8")

    assert "NEVER deletes or overwrites .env.secrets" in text
    assert "--exclude='.env.secrets'" in text
    assert "--exclude='.venv'" in text
    assert "--exclude='__pycache__'" in text
    assert "--exclude='.ruff_cache'" in text
    assert "--exclude='.pytest_cache'" in text
    assert "compose.yaml personal-rag.service env.example mcp embedding" in text
    assert 'rm -rf "$HOME/personal-rag"' not in text
    assert "cat >" not in text


def test_rag_stack_deployer_blocks_failed_readback_without_activation() -> None:
    text = _DEPLOY.read_text(encoding="utf-8")

    assert "expected_count" in text
    assert "remote_readback" in text
    assert "mcp/src/rag_mcp/app.py" in text
    assert "mcp/src/rag_mcp/store.py" in text
    assert "compose.yaml" in text
    assert "RAG-STACK-DEPLOY-BLOCK" in text
    executable = "\n".join(
        line for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "printf" not in line
    )
    assert not re.search(r"(^|[;&|]\s*)(docker|systemctl)(?:\s|$)", executable)
    assert (
        "COMPOSE_PROJECT_NAME=personal_rag docker compose --env-file .env.secrets "
        "-f compose.yaml up -d --build --no-deps mcp"
    ) in text


def test_activation_guidance_carries_the_project_name_and_no_deps() -> None:
    """The printed next step must be runnable AS PRINTED.

    The stack's systemd unit sets COMPOSE_PROJECT_NAME=personal_rag, so a hand-run
    compose that omits it derives `personal-rag` from the directory, fails to
    recognise the running containers as its own, and tries to stand up a PARALLEL
    stack — which dies on `Bind for 0.0.0.0:8765 failed: port is already allocated`
    while the old image keeps serving. Omitting --no-deps additionally drags
    embedding into the recreate and fails the same way on :8001. Both were measured
    on 2026-08-22: two activation attempts reported a built image while the running
    MCP stayed on the July source, and only the third (with both) recreated it.
    """
    from pathlib import Path as _Path

    script = (_Path(__file__).resolve().parents[2] / "automation/rag_stack/deploy.sh").read_text(
        encoding="utf-8"
    )
    guidance = [line for line in script.splitlines() if "up -d --build" in line]
    assert guidance, "the deployer no longer prints an activation next step"
    for line in guidance:
        assert "COMPOSE_PROJECT_NAME=personal_rag" in line, (
            f"activation guidance omits the unit's compose project: {line.strip()[:120]}"
        )
        assert "--no-deps" in line, (
            f"activation guidance would recreate embedding too: {line.strip()[:120]}"
        )
