#!/usr/bin/env bash
# Synchronize the canonical personal-RAG source tree to the configured RAG node.
# This source-only deployer NEVER deletes or overwrites .env.secrets: that untracked,
# mode-0600 file belongs to the node. Only the explicit release paths below move.
# Activation is deliberately separate because rebuilding MCP interrupts search and
# rebuilding embedding is substantially heavier. The owner/orchestrator activates.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${RAG_STACK_SSH_HOST:-$NODE_RAG_NODE_NAME}"

run_ops() {
  local script="$1"
  ssh "$host" "sudo -n -u ops -H bash -lc $(printf '%q' "$script")"
}

# shellcheck source=automation/deploy_provenance.sh
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/configs/rag" || exit 4

# Every remote payload enters $HOME first: `sudo -n -u ops` inherits the CALLER's
# cwd (an operator home ops cannot read), where find exits non-zero on "Failed to
# restore initial working directory" and takes the read-back's count with it
# (same defect fixed for the ingest deployer in #235).

# The lock prevents concurrent source syncs. Extraction names every managed top-level
# path instead of clearing personal-rag, so the node-owned .env.secrets is untouched.
tar -C "$repo_root/configs/rag" \
  --exclude='.env.secrets' --exclude='.venv' --exclude='__pycache__' \
  --exclude='.ruff_cache' --exclude='.pytest_cache' \
  -czf - compose.yaml personal-rag.service env.example mcp embedding \
  | run_ops 'cd "$HOME"; umask 077; root="$HOME/personal-rag"; exec 9>"$HOME/.personal-rag.deploy.lock"; if ! flock -w 300 9; then printf "RAG-STACK-DEPLOY-BLOCK: could not acquire source deployment lock within 300s\n" >&2; exit 6; fi; stage=$(mktemp -d "$HOME/.personal-rag.stage.XXXXXX"); trap '\''rm -rf -- "$stage"'\'' EXIT; tar -xzf - -C "$stage"; mkdir -p "$root"; install -m 0644 "$stage/compose.yaml" "$stage/personal-rag.service" "$stage/env.example" "$root/"; for component in mcp embedding; do rm -rf -- "$root/$component.previous"; if [[ -e "$root/$component" ]]; then mv -- "$root/$component" "$root/$component.previous"; fi; mv -- "$stage/$component" "$root/$component"; rm -rf -- "$root/$component.previous"; done'

# A successful transport is not deployment proof. Read back the complete managed file
# count plus the files that choose the MCP build and contain phase-five search logic.
expected_count="$(find "$repo_root/configs/rag" -type f \
  ! -name '.env.secrets' ! -path '*/.venv/*' ! -path '*/__pycache__/*' \
  ! -path '*/.ruff_cache/*' ! -path '*/.pytest_cache/*' | wc -l)"
readonly -a core_files=(mcp/src/rag_mcp/app.py mcp/src/rag_mcp/store.py compose.yaml)
remote_readback="$(run_ops 'cd "$HOME"; root="$HOME/personal-rag"; count=$(find "$root" -type f ! -name ".env.secrets" ! -path "*/.venv/*" ! -path "*/__pycache__/*" ! -path "*/.ruff_cache/*" ! -path "*/.pytest_cache/*" | wc -l); printf "count=%s\n" "$count"; for relative in mcp/src/rag_mcp/app.py mcp/src/rag_mcp/store.py compose.yaml; do hash=$(sha256sum -- "$root/$relative" | cut -d" " -f1) || exit; printf "%s|%s\n" "$hash" "$relative"; done' < /dev/null)"
remote_count="$(printf '%s\n' "$remote_readback" | awk -F= '/^count=/{print $2}')"
if [[ "$remote_count" != "$expected_count" ]]; then
  printf 'RAG-STACK-DEPLOY-BLOCK: file count mismatch (want=%s got=%s)\n' "$expected_count" "${remote_count:-unreadable}" >&2
  exit 5
fi
for core in "${core_files[@]}"; do
  want="$(sha256sum -- "$repo_root/configs/rag/$core" | cut -d' ' -f1)"
  got="$(printf '%s\n' "$remote_readback" | awk -F'|' -v path="$core" '$2 == path {print $1}')"
  if [[ "$got" != "$want" ]]; then
    printf 'RAG-STACK-DEPLOY-BLOCK: read-back mismatch for %s (want=%s got=%s)\n' "$core" "${want:0:16}" "${got:0:16}" >&2
    exit 5
  fi
done

printf 'RAG-STACK-DEPLOYED: source synchronized; .env.secrets preserved.\n'
#: COMPOSE_PROJECT_NAME mirrors personal-rag.service: without it compose derives
#: `personal-rag` from the directory, disowns the running containers and tries to
#: stand up a parallel stack (port already allocated) while the old image keeps
#: serving. --no-deps keeps embedding/qdrant untouched. Both measured 2026-08-22.
printf 'Next step (owner/orchestrator): cd /home/ops/personal-rag && COMPOSE_PROJECT_NAME=personal_rag docker compose --env-file .env.secrets -f compose.yaml up -d --build --no-deps mcp\n'
