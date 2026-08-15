#!/usr/bin/env bash
# W3-6 scenario driver (w3-report-hub): one synthetic Interop v0 report flows
# agent-bot -> #agents-log -> W3-4 collector -> reports.db -> dashboard, then
# every trace is removed (DB rows + Discord message) so the bank stays
# idempotent. Orchestrates two remote actuators (agent poster / ops observer)
# and judges the merged observations.
#
# Usage: w3_report_hub.sh <scenario.yaml> <report_dir>
set -euo pipefail

SCENARIO="$(readlink -f "$1")"
REPORT_DIR="$(mkdir -p "$2" && readlink -f "$2")"
ROOT="$(cd "$(dirname "$SCENARIO")/../../.." && pwd)"
DRIVERS="$ROOT/tests/e2e/drivers"
HOST="$(sed -n 's/^remote_host:[[:space:]]*//p' "$SCENARIO" | head -1)"
AGENT_PUSH=/home/agent/.cache/w36-hub-push
OPS_PUSH=/home/ops/.cache/w36-hub-push
RUN_TIMEOUT=300

remote() { # remote <account> <push_dir> <args...>
  local account="$1" push_dir="$2"; shift 2
  timeout "$RUN_TIMEOUT" ssh "$HOST" "sudo -n -u $account -H bash -lc \
    'set -euo pipefail; cd ~; python3 $push_dir/actor.py $*'" </dev/null
}

cleanup_remote() {
  ssh "$HOST" "sudo -n -u agent -H rm -rf $AGENT_PUSH" </dev/null || true
  ssh "$HOST" "sudo -n -u ops -H rm -rf $OPS_PUSH" </dev/null || true
}
trap cleanup_remote EXIT

ssh "$HOST" "sudo -n -u agent -H bash -c \
  'umask 077; rm -rf $AGENT_PUSH; mkdir -p $AGENT_PUSH; cat > $AGENT_PUSH/actor.py'" \
  < "$DRIVERS/w3_report_hub_agent.py"
ssh "$HOST" "sudo -n -u ops -H bash -c \
  'umask 077; rm -rf $OPS_PUSH; mkdir -p $OPS_PUSH; cat > $OPS_PUSH/actor.py'" \
  < "$DRIVERS/w3_report_hub_ops.py"

step() { # step <name> <account> <push_dir> <args...>
  local name="$1"; shift
  local log="$REPORT_DIR/$name.log"
  if ! remote "$@" >"$log" 2>&1; then
    echo "FAIL w3-report-hub: step $name failed (see $log)"
    exit 1
  fi
  sed -n 's/^OBS-JSON: //p' "$log" | head -1 >"$REPORT_DIR/obs-$name.json"
  if [[ ! -s "$REPORT_DIR/obs-$name.json" ]]; then
    echo "FAIL w3-report-hub: step $name emitted no OBS-JSON (see $log)"
    exit 1
  fi
}

step post agent "$AGENT_PUSH" post
read -r CHANNEL_ID MESSAGE_ID TASK_ID < <(python3 -c '
import json, sys
ctx = json.load(open(sys.argv[1]))["_ctx"]
print(ctx["channel_id"], ctx["message_id"], ctx["task_id"])' \
  "$REPORT_DIR/obs-post.json")

step ops ops "$OPS_PUSH" "$TASK_ID" "$MESSAGE_ID"
step del agent "$AGENT_PUSH" cleanup "$CHANNEL_ID" "$MESSAGE_ID"

python3 - "$REPORT_DIR" <<'PY'
import json
import sys
from pathlib import Path

report_dir = Path(sys.argv[1])
merged: dict[str, dict] = {}
for name in ("post", "ops", "del"):
    payload = json.loads((report_dir / f"obs-{name}.json").read_text(encoding="utf-8"))
    for case_id, fields in payload.items():
        if case_id.startswith("_"):
            continue
        merged.setdefault(case_id, {}).update(fields)
(report_dir / "observations.json").write_text(
    json.dumps(merged, ensure_ascii=False), encoding="utf-8"
)
PY

python3 "$DRIVERS/judge_expectations.py" "$SCENARIO" \
  "$REPORT_DIR/observations.json" "$REPORT_DIR/verdict.txt"
