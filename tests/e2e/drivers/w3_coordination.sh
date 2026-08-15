#!/usr/bin/env bash
# W3-6 scenario driver (w3-coordination): thin wrapper around the verified
# W3-3 E2E orchestrator tests/e2e/w3_3_coordination.sh (reuse, not reimplement).
# That script runs happy (live peer + signed-injection confirms + gated write
# + 120s #team cascade monitor + gated cleanup), refusal (1 renegotiation,
# 0 writes) and deadlock (peer stopped -> escalation DM, 0 writes), then
# verifies both gateways are active. This driver only extracts the script's
# self-asserted observables from its output and judges them against the YAML.
#
# Usage: w3_coordination.sh <scenario.yaml> <report_dir>
set -euo pipefail

SCENARIO="$(readlink -f "$1")"
REPORT_DIR="$(mkdir -p "$2" && readlink -f "$2")"
ROOT="$(cd "$(dirname "$SCENARIO")/../../.." && pwd)"
DRIVERS="$ROOT/tests/e2e/drivers"
RUN_TIMEOUT=1800

run_log="$REPORT_DIR/w3_3-run.log"
set +e
timeout "$RUN_TIMEOUT" bash "$ROOT/tests/e2e/w3_3_coordination.sh" \
  </dev/null >"$run_log" 2>&1
run_rc=$?
set -e

python3 - "$run_log" "$run_rc" "$REPORT_DIR/observations.json" <<'PY'
import json
import re
import sys

log = open(sys.argv[1], encoding="utf-8").read()
rc = int(sys.argv[2])
observations = {
    "happy": {
        "happy_pass": "W33 HAPPY-PASS corr=" in log,
        "cascade_notice_only": "cascade-safe: terse notice only" in log,
        "team_monitor_clean": bool(
            re.search(r"TEAM-AFTER envelopes=\d+ notices=1 others=0", log)
        ),
        "error": None,
    },
    "deadlock": {
        "deadlock_pass_full": bool(
            re.search(
                r"W33 DEADLOCK-PASS corr=coord-[0-9a-f]+ escalation_dm=found"
                r" calendar_writes=0",
                log,
            )
        ),
        "error": None,
    },
    "refusal": {
        "refusal_pass_full": bool(
            re.search(
                r"W33 REFUSAL-PASS corr=coord-[0-9a-f]+ renegotiations=1"
                r" calendar_writes=0",
                log,
            )
        ),
        "error": None,
    },
    "overall": {
        "script_exit": rc,
        "e2e_pass": "W3-3-E2E-PASS" in log,
        "gateway_agent_active": "gateway agent=active" in log,
        "gateway_peer_active": "gateway peer=active" in log,
        "error": None,
    },
}
with open(sys.argv[3], "w", encoding="utf-8") as handle:
    json.dump(observations, handle, ensure_ascii=False)
PY

python3 "$DRIVERS/judge_expectations.py" "$SCENARIO" \
  "$REPORT_DIR/observations.json" "$REPORT_DIR/verdict.txt"
