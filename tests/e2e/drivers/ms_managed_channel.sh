#!/usr/bin/env bash
# Offline MS-E1 scenario driver. All key material and local mocks live below
# the per-run temp directory and are removed after the judge has consumed OBS.
set -euo pipefail

SCENARIO="$(readlink -f "$1")"
REPORT_DIR="$(mkdir -p "$2" && readlink -f "$2")"
ROOT="$(cd "$(dirname "$SCENARIO")/../../.." && pwd)"
ACTOR="$ROOT/tests/e2e/drivers/ms_managed_channel_actor.py"
WORLD="$(mktemp -d -t ms-managed-channel.XXXXXX)"
RUN_LOG="$REPORT_DIR/actor.log"

cleanup() {
  rm -rf "$WORLD"
}
trap cleanup EXIT

set +e
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 "$ACTOR" --world "$WORLD" >"$RUN_LOG" 2>&1
ACTOR_RC=$?
set -e

if [[ $ACTOR_RC -ne 0 ]]; then
  echo "FAIL ms-managed-channel: actor rc=$ACTOR_RC (see $RUN_LOG)"
  exit 1
fi

sed -n 's/^OBS-JSON: //p' "$RUN_LOG" | head -1 >"$REPORT_DIR/observations.json"
if [[ ! -s "$REPORT_DIR/observations.json" ]]; then
  echo "FAIL ms-managed-channel: no OBS-JSON line (see $RUN_LOG)"
  exit 1
fi

python3 "$ROOT/tests/e2e/drivers/judge_expectations.py" "$SCENARIO" \
  "$REPORT_DIR/observations.json" "$REPORT_DIR/verdict.txt"
