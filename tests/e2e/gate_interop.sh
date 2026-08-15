#!/usr/bin/env bash
set -euo pipefail
readonly HOST="<primary-node>"
readonly DRIVER="/home/{account}/.hermes/interop/gate_driver.py"
run() { local account="$1" phase="$2" round="$3"; ssh "$HOST" "sudo -n -u $account -H bash -lc 'set -a; . /home/$account/.env.secrets; set +a; export PYTHONPATH=/home/$account/.hermes/interop_runtime; python3 ${DRIVER/\{account\}/$account} $account $phase $round'"; }
failed=0
for round in 1 2 3; do
  timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  a="$(run agent a "$round" || true)"
  b1="$(run agent b "$round" || true)"
  b2="$(run peer b "$round" || true)"
  b3="$(run agent b "$round" || true)"
  if [[ "$a" != *'"response": true, "dm": true'* || "$b2" != *'"parsed_other": true'* || "$b3" != *'"parsed_other": true'* ]]; then failed=1; fi
  printf 'round=%s timestamp=%s A=%s B-agent-post=%s B-peer=%s B-agent-parse=%s\n' "$round" "$timestamp" "$a" "$b1" "$b2" "$b3"
  [[ "$round" == 3 ]] || sleep 61
done
exit "$failed"
