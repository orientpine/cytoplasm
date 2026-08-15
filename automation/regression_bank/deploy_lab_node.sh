#!/usr/bin/env bash
set -euo pipefail

umask 077
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
RAG_NODE="$NODE_RAG_NODE_NAME"
PRIMARY_NODE="$NODE_PRIMARY_NODE_NAME"
RAG_TARGET="$NODE_OPERATOR_ACCOUNT@$RAG_NODE"
PRIMARY_TARGET="$NODE_OPERATOR_ACCOUNT@$PRIMARY_NODE"
OPERATOR_HOME="/home/$NODE_OPERATOR_ACCOUNT"
HARNESS_ROOT="$OPERATOR_HOME/autophagy-regression-bank"
RUNNER_PATH="$OPERATOR_HOME/.local/bin/autophagy-regression-bank-runner"
WEEKLY_LINE="15 3 * * 1 $RUNNER_PATH >> $OPERATOR_HOME/.cache/regression-bank-logs/cron.log 2>&1"
LEGACY_CRON_ID=56002e306644

rsync -a --delete \
  --exclude='.git/' \
  --exclude='configs/rag/' \
  --exclude='**/.venv/' \
  --exclude='**/node_modules/' \
  --exclude='**/__pycache__/' \
  --exclude='*.pyc' \
  --exclude='logs/' \
  --exclude='.omo/' \
  "$repo_root/" "$RAG_TARGET:$HARNESS_ROOT/"
ssh "$RAG_TARGET" "chmod 700 $HARNESS_ROOT; install -d -m 700 $OPERATOR_HOME/.local/bin $OPERATOR_HOME/.cache/regression-bank-logs"

scp "$repo_root/automation/regression_bank/remote_bank_runner.sh" "$RAG_TARGET:$RUNNER_PATH"
ssh "$RAG_TARGET" "chmod 700 $RUNNER_PATH"

existing_crontab="$(mktemp)"
filtered_crontab="$(mktemp)"
trap 'rm -f "$existing_crontab" "$filtered_crontab"' EXIT
ssh "$RAG_TARGET" 'crontab -l 2>/dev/null || true' >"$existing_crontab"
awk -v weekly_line="$WEEKLY_LINE" \
  '$0 != "CRON_TZ=Asia/Seoul" && $0 != weekly_line { print }' \
  "$existing_crontab" >"$filtered_crontab"
{
  printf '%s\n' 'CRON_TZ=Asia/Seoul'
  printf '%s\n' "$WEEKLY_LINE"
  cat "$filtered_crontab"
} | ssh "$RAG_TARGET" 'crontab -'

ssh "$PRIMARY_TARGET" "sudo -n -u agent -H bash -c 'umask 077; mkdir -p ~/.hermes/regression_bank_runtime; cat > ~/.hermes/regression_bank_runtime/bank_state.py; chmod 600 ~/.hermes/regression_bank_runtime/bank_state.py'" \
  <"$repo_root/automation/regression_bank/bank_state.py"

ssh "$PRIMARY_TARGET" "sudo -n -u agent -H env PATH=/home/agent/.local/bin:\$PATH hermes cron remove $LEGACY_CRON_ID 2>/dev/null || true"

printf '%s\n' '=== deployment verification ==='
ssh "$RAG_TARGET" "stat -c '%A %U %n' $HARNESS_ROOT; stat -c '%A %U %n' $RUNNER_PATH; crontab -l"
