#!/usr/bin/env bash
set -euo pipefail

umask 077
repo_root="${REGRESSION_BANK_HARNESS:-$HOME/autophagy-regression-bank}"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
HARNESS_ROOT="${REGRESSION_BANK_HARNESS:-$HOME/autophagy-regression-bank}"
STATE_HOST="${REGRESSION_BANK_STATE_HOST:-$NODE_PRIMARY_NODE_NAME}"
LOG_DIR="$HOME/.cache/regression-bank-logs"
LOCK_FILE="$HOME/.cache/regression-bank-runner.lock"

mkdir -p "$LOG_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  printf '%s\n' "regression-bank-runner: another run holds $LOCK_FILE; skipping"
  exit 0
fi

record_state() {
  local returncode="$1"
  ssh "$STATE_HOST" "sudo -n -u $NODE_AGENT_ACCOUNT -H python3 $NODE_AGENT_HOME/.hermes/regression_bank_runtime/bank_state.py record --returncode $returncode"
}

record_state 1

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
log_file="$LOG_DIR/bank-$timestamp.log"
set +e
timeout 1500 bash "$HARNESS_ROOT/tests/e2e/run_bank.sh" --all </dev/null >"$log_file" 2>&1
returncode=$?
set -e

record_state "$returncode"

shopt -s nullglob
mapfile -t bank_logs < <(printf '%s\n' "$LOG_DIR"/bank-*.log | sort -r)
for ((index = 10; index < ${#bank_logs[@]}; index++)); do
  rm -f -- "${bank_logs[$index]}"
done

exit "$returncode"
