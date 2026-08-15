#!/usr/bin/env bash
# Sandbox scenario for the coordination skill (W1-8 pipeline stage 1 / smoke).
# Fully offline: proves the pure state-machine invariants (no write command
# before both approvals, deadlock/refusal terminate with zero writes, exactly
# one renegotiation round, candidate cap 3) and the CLI's fail-closed behavior
# without a Discord token. No network, no real secrets.
set -euo pipefail

fail() { printf 'SCENARIO-FAIL %s\n' "$1" >&2; exit 1; }

secret="${AUTOPHAGY_DEMO_SECRET:-}"
[[ -n "$secret" ]] || fail "AUTOPHAGY_DEMO_SECRET is not set"
[[ "$secret" == DUMMY || "$secret" == DUMMY-* ]] || fail "secret is not DUMMY or DUMMY-* (real secrets forbidden in sandbox)"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_runtime="$(cd "$script_dir/../../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"
empty_home="$work/empty-home"
mkdir -p "$empty_home"

runtime="${INTEROP_RUNTIME:-$HOME/.hermes/interop_runtime}"
[[ -d "$runtime" ]] || runtime="$repo_runtime"
scenario_leg="pure-invariants"
if python3 -I -c "
import sys
sys.path.insert(0, '$runtime')
import automation.interop.coordination
" 2>/dev/null; then

python3 -I - "$runtime" <<'PY' || fail "pure state-machine invariants violated"
import sys
sys.path.insert(0, sys.argv[1])
from datetime import datetime, timedelta, timezone
from automation.interop import coordination as c

KST = timezone(timedelta(hours=9))
base = datetime(2026, 7, 17, 9, 0, tzinfo=KST)

def kinds(cmds):
    return [cmd.kind for cmd in cmds]

# candidate cap 3 + busy exclusion + range fit
peer = tuple((base + timedelta(hours=i)).isoformat() for i in range(8)) + ("bogus", "2026-07-17T12:00:00")
busy = ((base + timedelta(hours=1), base + timedelta(hours=2)),)
cands = c.candidate_slots(peer_slots=peer, busy=busy, range_start=base,
                          range_end=base + timedelta(hours=9), duration_min=30)
assert len(cands) == 3, cands
assert (base + timedelta(hours=1)).isoformat() not in cands

# happy path: write only after BOTH approvals
state, cmds = c.start(); assert kinds(cmds) == ["send_availability_query"]
state, cmds = c.on_availability(state, cands); assert kinds(cmds) == ["send_slot_confirm"]
state, cmds = c.on_peer_confirm(state, True); assert kinds(cmds) == ["request_owner_confirm"]
state, cmds = c.on_owner_confirm(state, True); assert kinds(cmds) == ["execute_calendar_write"]
state, cmds = c.on_executed(state)
assert kinds(cmds) == ["post_team_confirmation", "notify_result"]
assert state.phase is c.Phase.DONE

# deadlock timeout: escalation, no write command
state, _ = c.start()
state, cmds = c.on_timeout(state)
assert state.phase is c.Phase.DEADLOCK_TIMEOUT and kinds(cmds) == ["notify_escalation"]

# zero candidates: escalation, no write command
state, _ = c.start()
state, cmds = c.on_availability(state, ())
assert state.phase is c.Phase.DEADLOCK_NO_SLOTS and kinds(cmds) == ["notify_escalation"]

# refusal: exactly one renegotiation round, then terminate without writes
state, _ = c.start()
state, cmds = c.on_availability(state, cands)
state, cmds = c.on_peer_confirm(state, False)
assert kinds(cmds) == ["send_slot_confirm"] and state.renegotiated
state, cmds = c.on_peer_confirm(state, False)
assert state.phase is c.Phase.REFUSED and kinds(cmds) == ["notify_termination"]
print("PURE-INVARIANTS-OK")
PY

# CLI fail-closed without a Discord token (config error, nothing sent/written)
set +e
out="$(cd "$script_dir" && env -i HOME="$empty_home" PATH=/usr/bin:/bin E2E_TEST_MODE=1 INTEROP_RUNTIME="$runtime" \
  python3 coordinate_cli.py request --peer peer-test --summary demo \
  --range-start 2099-01-01T09:00:00+09:00 --range-end 2099-01-01T18:00:00+09:00 \
  --duration-min 30 --timeout-s 1 --e2e-confirm 2>&1)"; rc=$?
set -e
[[ "$rc" -eq 3 ]] || fail "tokenless CLI did not fail closed (rc=$rc: $out)"
grep -q "COORD-REFUSED" <<<"$out" || fail "tokenless CLI refusal marker missing"

else
  python3 -I - "$script_dir" <<'PY' || fail "staged fail-closed validation failed"
import os
import sys

sys.path.insert(0, sys.argv[1])
import coordinate_io

os.environ.pop("DISCORD_BOT_TOKEN", None)
try:
    coordinate_io.discord_bot_token()
except coordinate_io.CoordinationError as error:
    assert str(error) == "DISCORD_BOT_TOKEN 누락"
    assert error.exit_code == 3
else:
    raise AssertionError("missing Discord token was accepted")
PY
  scenario_leg="staged-fail-closed"
fi

printf 'SCENARIO-PASS leg=%s secret_len=%s account=%s\n' "$scenario_leg" "${#secret}" "$(whoami)"
