#!/usr/bin/env bash
# Shared restore for the owner-DM transactional deploy: returns run.py, adapter.py,
# the runtime modules, and the applier backups to their EXACT pre-deploy PRESENCE
# and bytes, using the present/absent manifest the transaction wrote at snapshot
# time ($SNAPSHOT_DIR/state). Used by BOTH owner-dm-txn.sh (auto-rollback) and
# deploy-owner-dm.sh (restart recovery) so both paths restore identically.
# Prints ROLLBACK-OK (exit 0) only when every restore + a final side-effect-free
# compile check succeeds; otherwise ROLLBACK-FAILED (exit 1) so the operator acts.
#
# Usage: owner-dm-restore.sh RUN_PY ADAPTER_PY ACTIVE_COMPAT_DIR SNAPSHOT_DIR
set -uo pipefail

if [ "$#" -ne 4 ]; then
  echo "usage: owner-dm-restore.sh RUN_PY ADAPTER_PY ACTIVE_COMPAT_DIR SNAPSHOT_DIR" >&2
  exit 2
fi
run_py="$1"; adapter_py="$2"; active="$3"; snap="$4"
ok=1

# Strict lookup: emit present/absent ONLY for a line that is exactly key=present or
# key=absent (one '=', a valid value). Malformed lines like key=absent=x yield nothing.
state_get() { awk -F= -v k="$1" 'NF==2 && $1==k && ($2=="present"||$2=="absent"){print $2}' "$snap/state" 2>/dev/null | head -1; }

# Fail closed if the manifest is missing / truncated / has an invalid value: a
# rollback that cannot know pre-run presence must NOT claim success.
for _key in bootstrap bootstrap_cache package automation_parent run_backup adapter_backup; do
  _n=$(grep -cE "^$_key=" "$snap/state" 2>/dev/null)
  _v=$(state_get "$_key")
  if [ "$_n" != 1 ] || { [ "$_v" != present ] && [ "$_v" != absent ]; }; then
    echo "ROLLBACK-FAILED — snapshot manifest missing/invalid key '$_key' in $snap/state" >&2
    exit 1
  fi
done

# Source files always existed pre-run; restore their exact bytes.
cp -p "$snap/run.py" "$run_py" || ok=0
cp -p "$snap/adapter.py" "$adapter_py" || ok=0

# Runtime bootstrap + package: restore if they existed, else REMOVE what the
# transaction activated (first-deploy case — they were absent pre-run).
case "$(state_get bootstrap)" in
  present) cp -p "$snap/runtime/hermes_compat_boot.py" "$active/hermes_compat_boot.py" || ok=0 ;;
  absent)  rm -f "$active/hermes_compat_boot.py" || ok=0 ;;
esac

# Restore the flat bootstrap bytecode cache to its EXACT pre-deploy state: a failed
# gateway start (recovery path) may have created/updated it. This matters for
# bootstrap=present (upgrade) too, where only the .py would otherwise be restored.
rm -rf "$active/__pycache__" || ok=0
if [ "$(state_get bootstrap_cache)" = present ]; then
  cp -a "$snap/runtime/__pycache__" "$active/__pycache__" || ok=0
fi
case "$(state_get package)" in
  present)
    rm -rf "$active/automation/hermes_compat" \
      && mkdir -p "$active/automation" \
      && cp -a "$snap/runtime/automation/hermes_compat" "$active/automation/hermes_compat" || ok=0
    ;;
  absent)
    rm -rf "$active/automation/hermes_compat" || ok=0
    ;;
esac
# If the automation parent dir itself was created by this deploy, remove it too
# (first-deploy: the package rollback above emptied it).
if [ "$(state_get automation_parent)" = absent ]; then
  rm -rf "$active/automation" || ok=0
fi

# Applier backups (*.autophagy-orig): remove any this deploy freshly created.
if [ "$(state_get run_backup)" = absent ]; then rm -f "$run_py.autophagy-orig" || ok=0; fi
if [ "$(state_get adapter_backup)" = absent ]; then rm -f "$adapter_py.autophagy-orig" || ok=0; fi
# Remove any half-written applier temp files a mid-write crash may have left.
rm -f "$run_py.autophagy-tmp" "$adapter_py.autophagy-tmp" || ok=0

if [ "$ok" -eq 1 ] && python3 -c 'import sys; [compile(open(f,"rb").read(), f, "exec") for f in sys.argv[1:]]' "$run_py" "$adapter_py"; then
  echo "ROLLBACK-OK (all files + presence restored to pre-deploy state)"
  exit 0
fi
echo "ROLLBACK-FAILED — MANUAL RECOVERY REQUIRED from $snap" >&2
exit 1
