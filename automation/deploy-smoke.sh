#!/usr/bin/env bash
set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly RUNNER="${DEPLOY_SMOKE_RUNNER:-$REPO_ROOT/automation/deploy-skill.sh}"
readonly STATE_PATH="${DEPLOY_SMOKE_STATE:-$HOME/.hermes/deploy-smoke/tick.json}"
readonly TIMESTAMP="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
readonly ARGV=(hello-autophagy --sandbox-only)

runner_rc=0
"$RUNNER" "${ARGV[@]}" || runner_rc=$?

state_dir="$(dirname "$STATE_PATH")"
umask 077
mkdir -p "$state_dir"
temporary="$(mktemp "$state_dir/.tick.XXXXXXXX")"
python3 - "$temporary" "$TIMESTAMP" "$runner_rc" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
timestamp = sys.argv[2]
exit_code = int(sys.argv[3])
payload = {
    "argv": ["hello-autophagy", "--sandbox-only"],
    "exit_code": exit_code,
    "outcome": "success" if exit_code == 0 else "failure",
    "timestamp": timestamp,
    "version": 1,
}
path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
PY
chmod 0600 "$temporary"
mv -f "$temporary" "$STATE_PATH"
exit "$runner_rc"
