#!/usr/bin/env bash
# Transactional core for the owner-DM patch deploy — path-based so it is unit
# testable with fault injection (no SSH). Runs ON the target node against LOCAL
# paths. Either commits ALL of {runtime modules, run.py, adapter.py} or restores
# every one of them to its pre-run bytes. Fail-closed: any error triggers restore.
#
# Usage:
#   owner-dm-txn.sh RUN_PY ADAPTER_PY BUSY_FIFO_APPLIER RECEIPTS_APPLIER \
#                   STAGING_DIR ACTIVE_COMPAT_DIR SNAPSHOT_DIR
#
# STAGING_DIR      holds the NEW hermes_compat_boot.py + automation/hermes_compat/*.py
# ACTIVE_COMPAT_DIR is the live import root (~/.hermes/hermes-compat) the gateway
#                   imports lazily; it is only mutated INSIDE this transaction.
# Exit 0 + "COMMIT-OK" on success; non-zero + "ROLLBACK-OK"/"ROLLBACK-FAILED" otherwise.
set -uo pipefail

if [ "$#" -ne 7 ]; then
  echo "usage: owner-dm-txn.sh RUN_PY ADAPTER_PY BUSY_FIFO_APPLIER RECEIPTS_APPLIER STAGING_DIR ACTIVE_COMPAT_DIR SNAPSHOT_DIR" >&2
  exit 2
fi
run_py="$1"; adapter_py="$2"; busy_applier="$3"; receipts_applier="$4"
staging="$5"; active="$6"; snap="$7"
committed=0

fail() { echo "TXN-FAIL: $1" >&2; exit "${2:-1}"; }

# 1. Snapshot EVERY file this transaction may mutate, before touching anything.
mkdir -p "$snap" || fail "cannot create snapshot dir $snap" 10
cp -p "$run_py" "$snap/run.py" || fail "snapshot run.py failed" 10
cp -p "$adapter_py" "$snap/adapter.py" || fail "snapshot adapter.py failed" 10
mkdir -p "$snap/runtime" || fail "cannot create snapshot runtime dir" 10
if [ -e "$active/hermes_compat_boot.py" ]; then
  cp -p "$active/hermes_compat_boot.py" "$snap/runtime/hermes_compat_boot.py" || fail "snapshot bootstrap failed" 10
fi
if [ -d "$active/automation/hermes_compat" ]; then
  mkdir -p "$snap/runtime/automation" || fail "snapshot pkg parent failed" 10
  cp -a "$active/automation/hermes_compat" "$snap/runtime/automation/hermes_compat" || fail "snapshot pkg failed" 10
fi
if [ -d "$active/__pycache__" ]; then
  cp -a "$active/__pycache__" "$snap/runtime/__pycache__" || fail "snapshot bootstrap cache failed" 10
fi
# Record pre-run PRESENCE so the shared restore can remove first-deploy artifacts
# (runtime/backup files that did not exist before this deploy) instead of leaving them.
{
  printf 'bootstrap=%s\n' "$([ -e "$active/hermes_compat_boot.py" ] && echo present || echo absent)"
  printf 'bootstrap_cache=%s\n' "$([ -d "$active/__pycache__" ] && echo present || echo absent)"
  printf 'package=%s\n' "$([ -d "$active/automation/hermes_compat" ] && echo present || echo absent)"
  printf 'automation_parent=%s\n' "$([ -d "$active/automation" ] && echo present || echo absent)"
  printf 'run_backup=%s\n' "$([ -e "$run_py.autophagy-orig" ] && echo present || echo absent)"
  printf 'adapter_backup=%s\n' "$([ -e "$adapter_py.autophagy-orig" ] && echo present || echo absent)"
} > "$snap/state" || fail "cannot write snapshot state manifest" 10

# 2. Arm the restore trap. It fires on ANY non-committed exit and must itself
#    succeed (compile-verified) or shout ROLLBACK-FAILED so the operator acts.
restore() {
  if [ "$committed" -eq 1 ]; then
    return 0
  fi
  echo "TXN: restoring pre-deploy state from snapshot" >&2
  bash "$(dirname "$0")/owner-dm-restore.sh" "$run_py" "$adapter_py" "$active" "$snap" >&2
}
trap restore EXIT

# 3. Activate the staged runtime modules into the live import root.
[ -f "$staging/hermes_compat_boot.py" ] || fail "staging bootstrap missing" 11
cp -p "$staging/hermes_compat_boot.py" "$active/hermes_compat_boot.py" || fail "activate bootstrap failed" 11
mkdir -p "$active/automation/hermes_compat" || fail "activate pkg dir failed" 11
cp -p "$staging"/automation/hermes_compat/*.py "$active/automation/hermes_compat/" || fail "activate pkg failed" 11

# 4. Apply BOTH source patches (each self-backs-up + compile-gates), then an
#    independent py_compile, then a marker verify. Any failure -> trap restores.
python3 -I "$busy_applier" apply "$run_py" || fail "busy-fifo apply failed" 12
python3 -c 'import sys; compile(open(sys.argv[1],"rb").read(), sys.argv[1], "exec")' "$run_py" || fail "run.py compile failed" 12
python3 -I "$busy_applier" verify "$run_py" || fail "busy-fifo verify failed" 12
python3 -I "$receipts_applier" apply "$adapter_py" || fail "receipts apply failed" 12
python3 -c 'import sys; compile(open(sys.argv[1],"rb").read(), sys.argv[1], "exec")' "$adapter_py" || fail "adapter.py compile failed" 12
python3 -I "$receipts_applier" verify "$adapter_py" || fail "receipts verify failed" 12

# 5. Commit: disarm the restore trap; the snapshot is kept for restart rollback.
committed=1
trap - EXIT
echo "COMMIT-OK (snapshot retained at $snap)"
