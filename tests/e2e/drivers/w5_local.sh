#!/usr/bin/env bash
set -euo pipefail

SCENARIO="$(readlink -f "$1")"
REPORT_DIR="$(mkdir -p "$2" && readlink -f "$2")"
ROOT="$(cd "$(dirname "$SCENARIO")/../../.." && pwd)"
DRIVERS="$ROOT/tests/e2e/drivers"
NAME="$(basename "$SCENARIO" .yaml)"
ACTOR="$(sed -n 's/^actor:[[:space:]]*//p' "$SCENARIO" | head -1)"
RUN_TIMEOUT=600

if [[ -z "$ACTOR" || ! -f "$ROOT/$ACTOR" ]]; then
  echo "FAIL $NAME: actor missing: ${ACTOR:-<none>}"
  exit 1
fi

run_log="$REPORT_DIR/actor-run.log"
set +e
timeout "$RUN_TIMEOUT" python3 "$ROOT/$ACTOR" --root "$ROOT" \
  </dev/null >"$run_log" 2>&1
run_rc=$?
set -e

if [[ $run_rc -ne 0 ]]; then
  echo "FAIL $NAME: actor rc=$run_rc (see $run_log)"
  exit 1
fi

sed -n 's/^OBS-JSON: //p' "$run_log" | head -1 >"$REPORT_DIR/observations.json"
if [[ ! -s "$REPORT_DIR/observations.json" ]]; then
  echo "FAIL $NAME: no OBS-JSON line in actor output (see $run_log)"
  exit 1
fi

python3 "$DRIVERS/judge_expectations.py" "$SCENARIO" \
  "$REPORT_DIR/observations.json" "$REPORT_DIR/verdict.txt"
