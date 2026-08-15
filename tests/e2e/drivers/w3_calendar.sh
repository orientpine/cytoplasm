#!/usr/bin/env bash
# W3-6 scenario driver (w3-calendar): pushes the remote actuator to the agent
# account, runs it UNATTENDED (per-run HMAC secret, E2E mode scoped to the
# actuator process tree only), then judges the emitted observations.
#
# Usage: w3_calendar.sh <scenario.yaml> <report_dir>
# Exit:  0 = every case matched its expected observables, 1 = mismatch/error.
set -euo pipefail

SCENARIO="$(readlink -f "$1")"
REPORT_DIR="$(mkdir -p "$2" && readlink -f "$2")"
ROOT="$(cd "$(dirname "$SCENARIO")/../../.." && pwd)"
DRIVERS="$ROOT/tests/e2e/drivers"
HOST="$(sed -n 's/^remote_host:[[:space:]]*//p' "$SCENARIO" | head -1)"
ACCOUNT="$(sed -n 's/^remote_account:[[:space:]]*//p' "$SCENARIO" | head -1)"
PUSH_DIR="/home/$ACCOUNT/.cache/w36-calendar-push"
RUN_TIMEOUT=600

# Push actuator + generate the per-run injection secret (agent-home, 600).
ssh "$HOST" "sudo -n -u $ACCOUNT -H bash -c \
  'umask 077; rm -rf $PUSH_DIR; mkdir -p $PUSH_DIR; cat > $PUSH_DIR/remote.py'" \
  < "$DRIVERS/w3_calendar_remote.py"
ssh "$HOST" "sudo -n -u $ACCOUNT -H bash -c \
  'umask 077; openssl rand -hex 32 > $PUSH_DIR/.secret'" </dev/null

run_log="$REPORT_DIR/remote-run.log"
set +e
timeout "$RUN_TIMEOUT" ssh "$HOST" "sudo -n -u $ACCOUNT -H bash -lc \
  'set -euo pipefail; cd ~; python3 $PUSH_DIR/remote.py \
   --secret-file $PUSH_DIR/.secret'" </dev/null >"$run_log" 2>&1
run_rc=$?
set -e

ssh "$HOST" "sudo -n -u $ACCOUNT -H rm -rf $PUSH_DIR" </dev/null || true

if [[ $run_rc -ne 0 ]]; then
  echo "FAIL w3-calendar: remote actuator rc=$run_rc (see $run_log)"
  exit 1
fi

sed -n 's/^OBS-JSON: //p' "$run_log" | head -1 >"$REPORT_DIR/observations.json"
if [[ ! -s "$REPORT_DIR/observations.json" ]]; then
  echo "FAIL w3-calendar: no OBS-JSON line in remote output (see $run_log)"
  exit 1
fi

python3 "$DRIVERS/judge_expectations.py" "$SCENARIO" \
  "$REPORT_DIR/observations.json" "$REPORT_DIR/verdict.txt"
