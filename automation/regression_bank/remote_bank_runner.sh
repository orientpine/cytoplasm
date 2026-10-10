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

# 매 회 하네스를 주 노드가 지금 돌리는 릴리스 트리로 맞춘다. 하네스는 deploy_lab_node.sh 를 손으로 돌릴 때만
# 갱신됐고, 2026-07-20 사본이 그대로 남아 스킬 루트 반전(2026-08-15) 뒤 없어진 경로를 부르며 두 달간
# 매주 실패했다. 릴리스 트리는 서명 검증을 거친 그 코드다. 푸시 전 작업 트리를 시험하려면
# REGRESSION_BANK_REFRESH=0 으로 끄고 deploy_lab_node.sh 로 옮긴다.
if [[ "${REGRESSION_BANK_REFRESH:-1}" != 0 ]]; then
  source_tree="${REGRESSION_BANK_SOURCE:-$STATE_HOST:$NODE_RELEASE_CURRENT/}"
  if ! rsync -a --delete \
      --exclude='.git/' --exclude='configs/rag/' --exclude='**/.venv/' \
      --exclude='**/node_modules/' --exclude='**/__pycache__/' --exclude='*.pyc' \
      --exclude='logs/' --exclude='.omo/' \
      "$source_tree" "$HARNESS_ROOT/"; then
    printf '%s\n' "BANK-HARNESS-REFRESH-FAIL: could not sync $source_tree into $HARNESS_ROOT" >&2
    exit 1
  fi
  printf '%s\n' "BANK-HARNESS-REFRESHED from $source_tree"
fi

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
