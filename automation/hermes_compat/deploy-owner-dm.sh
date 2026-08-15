#!/usr/bin/env bash
# Deploy the owner-DM busy-FIFO segmentation + per-message receipts patches.
#
# TRANSACTIONAL & FAIL-CLOSED (owner-gated — restarts the live gateway):
#   1. Push appliers + owner-dm-txn.sh + owner-dm-drain-check.py to an appliers dir
#      and the NEW runtime modules + bootstrap to a STAGING dir. Neither is on the
#      gateway's live import path, so the running gateway is untouched so far.
#   2. PREFLIGHT: apply+compile+verify BOTH patches on throwaway COPIES; abort
#      before any live mutation if either would fail on the real files.
#   3. DRAIN-GUARD (fail-closed, session-bound): refuse if the content-free ledger
#      has ANY unresolved (received) owner-DM receipt — a turn is mid-flight — or if
#      the state cannot be determined. Override only via ALLOW_INFLIGHT_RESTART=1.
#   4. TRANSACTION (owner-dm-txn.sh): snapshot run.py + adapter.py + the live runtime
#      modules, then activate the staged modules and apply both source patches. ANY
#      failure auto-restores every one of those files to its pre-deploy bytes.
#   5. RESTART agent + peer together (AGENTS.md 2026-07-22), verifying each becomes
#      active. If either fails, restore ALL files from the snapshot and restart again.
#
# set -e aborts before the transaction if preflight or the drain-guard fails.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-$NODE_DEPLOY_SSH_HOST}"
hc="$repo_root/automation/hermes_compat"
compat='.hermes/hermes-compat'
run_py='.hermes/hermes-agent/gateway/run.py'
adapter_py='.hermes/hermes-agent/plugins/platforms/discord/adapter.py'
ts="$(date +%Y%m%d-%H%M%S)"
allow_inflight="${ALLOW_INFLIGHT_RESTART:-0}"

# 0. Deploy guard: a patched live gateway that nobody can reproduce from git is the
#    worst form of the silent-revert failure, so refuse anything origin/main lacks
#    (automation/deploy_provenance.sh).
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$hc" || exit 4

# 1a. Push appliers + the transactional core + the drain-check helper (tools; the
#     gateway never imports these, so this cannot affect the running process).
tar -C "$hc" -czf - patch_busy_fifo.py patch_discord_receipts.py owner-dm-txn.sh owner-dm-restore.sh owner-dm-drain-check.py \
  | ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "umask 077; mkdir -p \"\$HOME/$compat/appliers\"; tar -xzf - -C \"\$HOME/$compat/appliers\"")"

# 1b. Stage the NEW runtime modules + bootstrap OFF the live import path. The
#     transaction (step 4) activates them atomically-with-rollback.
tar -C "$hc" -czf - hermes_compat_boot.py \
  | ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "umask 077; mkdir -p \"\$HOME/$compat/staging\"; tar -xzf - -C \"\$HOME/$compat/staging\"")"
tar -C "$hc" -czf - __init__.py owner_dm_relatedness.py owner_dm_signal.py owner_dm_dispatch.py receipt_ledger.py receipt_tracker.py receipt_apply.py \
  | ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "umask 077; mkdir -p \"\$HOME/$compat/staging/automation/hermes_compat\"; tar -xzf - -C \"\$HOME/$compat/staging/automation/hermes_compat\"")"

# 2. PREFLIGHT on throwaway copies — prove BOTH patches apply+compile+verify
#    against the real live source BEFORE touching it. Aborts (set -e) on any drift.
ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "\
  set -euo pipefail; \
  A=\"\$HOME/$compat/appliers\"; \
  P=\$(mktemp -d); trap 'rm -rf \"\$P\"' EXIT; \
  cp \"\$HOME/$run_py\" \"\$P/run.py\"; cp \"\$HOME/$adapter_py\" \"\$P/adapter.py\"; \
  python3 -I \"\$A/patch_busy_fifo.py\" apply \"\$P/run.py\"; \
  python3 -m py_compile \"\$P/run.py\"; \
  python3 -I \"\$A/patch_busy_fifo.py\" verify \"\$P/run.py\"; \
  python3 -I \"\$A/patch_discord_receipts.py\" apply \"\$P/adapter.py\"; \
  python3 -m py_compile \"\$P/adapter.py\"; \
  python3 -I \"\$A/patch_discord_receipts.py\" verify \"\$P/adapter.py\"; \
  echo 'PREFLIGHT: both patches apply+compile+verify cleanly'")"

# 3. DRAIN-GUARD (fail-closed): the ledger's unresolved-receipt count is a
#    session/message-bound in-flight signal; unknown state counts as in-flight.
ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "\
  set -uo pipefail; \
  A=\"\$HOME/$compat/appliers\"; \
  allow=\"$allow_inflight\"; \
  if recv=\$(python3 \"\$A/owner-dm-drain-check.py\" \"\$HOME/$compat/staging\" 2>/dev/null); then \
    case \"\$recv\" in \
      (0) state=clear ;; \
      (''|*[!0-9]*|0*) state=undetermined ;; \
      (*) state=\"\$recv unresolved receipt(s)\" ;; \
    esac; \
  else \
    state=undetermined; \
  fi; \
  if [ \"\$state\" = clear ]; then \
    echo 'DRAIN-GUARD: clear (no unresolved receipts)'; \
  elif [ \"\$allow\" = 1 ]; then \
    echo \"DRAIN-GUARD: \$state — proceeding (ALLOW_INFLIGHT_RESTART=1); framework drain applies.\" >&2; \
  else \
    echo \"DRAIN-GUARD: \$state — refusing (fail-closed). Retry in a quiet window, or set ALLOW_INFLIGHT_RESTART=1.\" >&2; exit 20; \
  fi")"

# 4. TRANSACTION: snapshot + activate staged modules + apply both source patches,
#    with automatic restore of EVERY mutated file on any failure.
ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "\
  set -euo pipefail; \
  A=\"\$HOME/$compat/appliers\"; \
  bash \"\$A/owner-dm-txn.sh\" \
    \"\$HOME/$run_py\" \"\$HOME/$adapter_py\" \
    \"\$A/patch_busy_fifo.py\" \"\$A/patch_discord_receipts.py\" \
    \"\$HOME/$compat/staging\" \"\$HOME/$compat\" \"\$HOME/$compat/rollback/$ts\"; \
  ls -1dt \"\$HOME/$compat/rollback\"/*/ 2>/dev/null | tail -n +6 | xargs -r rm -rf || true")"

# 5. RESTART BOTH gateways together (gateway restart rule, AGENTS.md 2026-07-22).
#    On failure, restore EVERY file from the snapshot and restart again to recover.
restart_one() {
  local acct="$1"
  ssh "$host" "sudo -n -u $acct -H bash -lc 'export XDG_RUNTIME_DIR=/run/user/\$(id -u); systemctl --user restart hermes-gateway.service && systemctl --user is-active hermes-gateway.service'"
}
restore_from_snapshot() {
  ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "\
    A=\"\$HOME/$compat/appliers\"; \
    bash \"\$A/owner-dm-restore.sh\" \"\$HOME/$run_py\" \"\$HOME/$adapter_py\" \"\$HOME/$compat\" \"\$HOME/$compat/rollback/$ts\"")"
}
# Restart agent AND peer INDEPENDENTLY (both always attempted); each remote runs
# restart && is-active so a failed restart is not masked by a still-active unit.
agent_ok=1; peer_ok=1
restart_one agent || agent_ok=0
restart_one peer || peer_ok=0
if [ "$agent_ok" -ne 1 ] || [ "$peer_ok" -ne 1 ]; then
  echo "RESTART-FAILED (agent=$agent_ok peer=$peer_ok) — restoring from snapshot and restarting to recover." >&2
  restore_ok=1
  restore_from_snapshot >&2 || restore_ok=0
  rec_agent=1; rec_peer=1
  restart_one agent || rec_agent=0
  restart_one peer || rec_peer=0
  if [ "$restore_ok" -ne 1 ] || [ "$rec_agent" -ne 1 ] || [ "$rec_peer" -ne 1 ]; then
    echo "ROLLBACK-RECOVERY-FAILED (restore=$restore_ok agent=$rec_agent peer=$rec_peer) — MANUAL INTERVENTION REQUIRED." >&2
    exit 41
  fi
  echo "Deploy rolled back and both gateways recovered. Investigate before retrying." >&2
  exit 40
fi
echo "DEPLOY-OK (agent + peer restarted; rollback snapshot kept at ~/$compat/rollback/$ts)"
