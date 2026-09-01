#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}"
if [ -z "$host" ]; then
  echo "DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset. Export it (or configure ~/.hermes/node.toml)" >&2
  echo "              and re-run; refusing to ssh to an unresolvable placeholder." >&2
  exit 3
fi

push_tree() {
  local source="$1" destination="$2"
  deploy_archive_stream "$repo_root" "$(dirname "$source")" "$(basename "$source")" \
    | ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "umask 077; mkdir -p \"\$HOME/$destination\"; tar -xzf - -C \"\$HOME/$destination\"")"
}

push_contents() {
  local source="$1" destination="$2"
  deploy_archive_stream "$repo_root" "$source" . \
    | ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "umask 077; mkdir -p \"\$HOME/$destination\"; tar -xzf - -C \"\$HOME/$destination\"")"
}

# Deploy guard: refuse to push code that origin/main does not have (see the header of
# automation/deploy_provenance.sh for why a silent revert is otherwise inevitable).
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/skill_generation" || exit 4

push_tree "$repo_root/automation/skill_generation" '.hermes/skill-generation/runtime/automation'
push_contents "$repo_root/automation/skill_generation/plugin" '.hermes/plugins/05-skill-generation'
ssh "$host" "sudo -n -u agent -H bash -lc 'PATH=\"\$HOME/.local/bin:\$PATH\"; hermes plugins enable 05-skill-generation; XDG_RUNTIME_DIR=/run/user/\$(id -u) systemctl --user restart hermes-gateway.service; hermes plugins list --plain --no-bundled'"
# 게이트웨이 재시동 규칙(AGENTS.md, 2026-07-22): agent만 재시동하지 않는다 — peer gateway도 함께 재시동.
ssh "$host" "sudo -n -u peer -H bash -lc 'export XDG_RUNTIME_DIR=/run/user/\$(id -u); systemctl --user restart hermes-gateway.service; systemctl --user is-active hermes-gateway.service'"
