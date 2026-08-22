#!/usr/bin/env bash
# automation/rag_ingest/deploy.sh — deploy the personal-RAG ingest runtime + watcher.
#
# Deploys the WHOLE rag_ingest package (preserving the package dir) to
# ~agent/.hermes/rag_ingest_runtime/rag_ingest/ via tar-over-ssh, so the wrapper's
# `sys.path.insert(RUNTIME_DIR); from rag_ingest.cli import ...` resolves. tar (not
# cp) avoids the cp-into-existing-dir nesting footgun; replacing the runtime clears
# stale __pycache__ left by an older interpreter. cron/ is excluded because its
# wrapper has a separate, manifest-observed destination in the Hermes scripts directory.
#
# The package replacement holds the watcher's own flock. A tick can therefore run
# before or after deployment, never while a partially extracted package is visible.
# Provenance enforces commit -> push -> deploy; origin/main must already contain it.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-$NODE_DEPLOY_SSH_HOST}"

run_agent() {
  local script="$1"
  ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "$script")"
}

# push_file performs a remote sha256 read-back for the separately deployed wrapper.
# shellcheck source=automation/deploy_push.sh
source "$repo_root/automation/deploy_push.sh"
# shellcheck source=automation/deploy_provenance.sh
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/rag_ingest" || exit 4

# The remote shell inherits the CALLER's cwd (an operator home the target account
# cannot read), so every payload enters $HOME first: otherwise find exits non-zero on
# "Failed to restore initial working directory" and set -e kills the deploy between
# the tar extract and the read-back (2026-08-22 production defect).

# The 300-second bounded wait makes a busy first Obsidian ingest explicit rather than
# deploying around it. Failure leaves the existing package untouched and returns rc 6.
tar -C "$repo_root/automation" --exclude='__pycache__' --exclude='rag_ingest/cron' -czf - rag_ingest \
  | run_agent 'cd "$HOME"; umask 077; mkdir -p "$HOME/.hermes/rag-ingest"; exec 9>"$HOME/.hermes/rag-ingest/watch.lock"; if ! flock -w 300 9; then printf "RAG-DEPLOY-BLOCK: could not acquire $HOME/.hermes/rag-ingest/watch.lock within 300s; ingest is still running\n" >&2; exit 6; fi; rm -rf "$HOME/.hermes/rag_ingest_runtime"; mkdir -p "$HOME/.hermes/rag_ingest_runtime"; tar -xzf - -C "$HOME/.hermes/rag_ingest_runtime"; find "$HOME/.hermes/rag_ingest_runtime" -type d -name __pycache__ -prune -exec rm -rf {} +; find "$HOME/.hermes/rag_ingest_runtime" -type d -exec chmod 700 {} +; find "$HOME/.hermes/rag_ingest_runtime" -type f -name "*.py" -exec chmod 600 {} +'

# Read back both package shape and representative import-path files. A successful tar
# exit without this check is not deployment proof (2026-08-20 push incident).
expected_count="$(find "$repo_root/automation/rag_ingest" -type f -name '*.py' ! -path '*/cron/*' ! -path '*/__pycache__/*' | wc -l)"
readonly -a core_files=(__init__.py cli.py sources/obsidian.py)
remote_readback="$(run_agent 'cd "$HOME"; root="$HOME/.hermes/rag_ingest_runtime/rag_ingest"; count=$(find "$root" -type f -name "*.py" ! -path "*/__pycache__/*" | wc -l); printf "count=%s\n" "$count"; sha256sum "$root/__init__.py" "$root/cli.py" "$root/sources/obsidian.py"' < /dev/null)"
remote_count="$(printf '%s\n' "$remote_readback" | awk -F= '/^count=/{print $2}')"
if [[ "$remote_count" != "$expected_count" ]]; then
  printf 'RAG-DEPLOY-BLOCK: runtime file count mismatch (want=%s got=%s)\n' "$expected_count" "${remote_count:-unreadable}" >&2
  exit 5
fi
for core in "${core_files[@]}"; do
  want="$(sha256sum -- "$repo_root/automation/rag_ingest/$core" | cut -d' ' -f1)"
  got="$(printf '%s\n' "$remote_readback" | awk -v suffix="/rag_ingest/$core" '$2 ~ suffix "$" {print $1}')"
  if [[ "$got" != "$want" ]]; then
    printf 'RAG-DEPLOY-BLOCK: runtime read-back mismatch for %s (want=%s got=%s)\n' "$core" "${want:0:16}" "${got:0:16}" >&2
    exit 5
  fi
done

push_file "$repo_root/automation/rag_ingest/cron/rag_ingest_watch.py" '.hermes/scripts/rag_ingest_watch.py'

# --all sees paused jobs too, so rerunning cannot create a duplicate cron entry.
run_agent 'PATH="$HOME/.local/bin:$PATH"; if hermes cron list --all | grep -Eq "Name:[[:space:]]+rag-ingest-watch$"; then exit 0; fi; hermes cron create "every 10m" --name rag-ingest-watch --no-agent --script rag_ingest_watch.py --deliver local'
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list --all | grep -A3 rag-ingest-watch || true'
