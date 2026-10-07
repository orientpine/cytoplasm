#!/usr/bin/env bash
# Usage: automation/skill_generation/deploy.sh [--restart]
# Ships the plugin and its runtime copy exactly as deploy-manifest.txt declares them. Without
# --restart no gateway is touched: the runtime rows carry activation=gateway, so deploy_all
# restarts the agent+peer pair when they drift. --restart is for a standalone run.
set -euo pipefail

restart=0
case "${1-}" in
  "") ;;
  --restart) restart=1 ;;
  *) echo "usage: $0 [--restart]" >&2; exit 2 ;;
esac
[ $# -le 1 ] || { echo "usage: $0 [--restart]" >&2; exit 2; }

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}"
if [ -z "$host" ]; then
  echo "DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset. Export it (or configure ~/.hermes/node.toml)" >&2
  echo "              and re-run; refusing to ssh to an unresolvable placeholder." >&2
  exit 3
fi

run_account() { # <account> <script>
  ssh "$host" "sudo -n -u $(printf '%q' "$1") -H bash -lc $(printf '%q' "cd \"\$HOME\"; $2")"
}
run_agent() { run_account "$NODE_AGENT_ACCOUNT" "$1"; }

# Deploy guard: refuse to push code that origin/main does not have (see the header of
# automation/deploy_provenance.sh for why a silent revert is otherwise inevitable).
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" \
  "$repo_root/automation/skill_generation" "$repo_root/automation/selfskill_audit" \
  "$repo_root/automation/skill_review.py" "$repo_root/automation/skill_mount.py" \
  "$repo_root/automation/scenario_runner.py" || exit 4

# shellcheck source=automation/deploy_push.sh
source "$repo_root/automation/deploy_push.sh"
# shellcheck source=automation/deploy_tree.sh
source "$repo_root/automation/deploy_tree.sh"

runtime='.hermes/skill-generation/runtime/automation'
# `|| exit` keeps errexit off inside the helper, so a failed remote step still runs its staging cleanup.
deploy_tree_swap "$repo_root/automation/skill_generation" "$runtime/skill_generation" || exit "$?"
deploy_tree_swap "$repo_root/automation/selfskill_audit" "$runtime/selfskill_audit" || exit "$?"
push_file "$repo_root/automation/skill_review.py" "$runtime/skill_review.py"
push_file "$repo_root/automation/skill_mount.py" "$runtime/skill_mount.py"
push_file "$repo_root/automation/scenario_runner.py" "$runtime/scenario_runner.py"
push_file "$repo_root/automation/skill_generation/plugin/__init__.py" \
  '.hermes/plugins/05-skill-generation/__init__.py'
push_file "$repo_root/automation/skill_generation/plugin/plugin.yaml" \
  '.hermes/plugins/05-skill-generation/plugin.yaml'
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes plugins enable 05-skill-generation < /dev/null'

if ((restart)); then
  # 게이트웨이 재시동 규칙(AGENTS.md, 2026-07-22): agent 만 재시동하지 않는다 — peer 도 함께, agent 다음에.
  for pair in "$NODE_AGENT_ACCOUNT:$NODE_AGENT_GATEWAY_UNIT" "$NODE_PEER_ACCOUNT:$NODE_PEER_GATEWAY_UNIT"; do
    unit="$(printf '%q' "${pair#*:}")"
    run_account "${pair%%:*}" "export XDG_RUNTIME_DIR=/run/user/\$(id -u); systemctl --user restart $unit && systemctl --user is-active $unit" < /dev/null
  done
fi
