#!/usr/bin/env bash
# Deploy the Hermes gateway compatibility patch (busy-path pre_gateway_dispatch).
#
# Order is safety-critical: push -> apply (exact-preimage, backup, compile-checked)
# -> py_compile the live file -> verify -> ONLY THEN restart. set -e aborts before
# any restart if the patch or a syntax check fails, so a bad patch never reaches a
# gateway restart. Per the 2026-07-22 rule, agent and peer gateways restart together.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-$NODE_DEPLOY_SSH_HOST}"
run_py='.hermes/hermes-agent/gateway/run.py'
dest='.hermes/hermes-compat'

# Deploy guard: the gateway patch carrier must exist in origin/main first — a patched
# gateway that no one can reproduce from git is the worst version of the silent-revert
# failure (see automation/deploy_provenance.sh).
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/hermes_compat" || exit 4

# 1. Push the carrier (patcher + manifest) to the agent account.
deploy_archive_stream "$repo_root" "$repo_root/automation" hermes_compat \
  | ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "umask 077; mkdir -p \"\$HOME/$dest\"; tar -xzf - -C \"\$HOME/$dest\"")"

# 2. Apply (idempotent, exact-preimage; backs up run.py to run.py.autophagy-orig),
#    then independently py_compile the live file, then verify the markers.
ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "\
  set -euo pipefail; \
  python3 -I \"\$HOME/$dest/hermes_compat/patch_busy_dispatch.py\" apply \"\$HOME/$run_py\"; \
  python3 -m py_compile \"\$HOME/$run_py\"; \
  python3 -I \"\$HOME/$dest/hermes_compat/patch_busy_dispatch.py\" verify \"\$HOME/$run_py\"")"

# 3. Restart BOTH gateways together (gateway restart rule, AGENTS.md 2026-07-22).
ssh "$host" "sudo -n -u agent -H bash -lc 'export XDG_RUNTIME_DIR=/run/user/\$(id -u); systemctl --user restart hermes-gateway.service; systemctl --user is-active hermes-gateway.service'"
ssh "$host" "sudo -n -u peer -H bash -lc 'export XDG_RUNTIME_DIR=/run/user/\$(id -u); systemctl --user restart hermes-gateway.service; systemctl --user is-active hermes-gateway.service'"
