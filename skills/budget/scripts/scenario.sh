#!/usr/bin/env bash
# Sandbox scenario for the budget skill (W1-8 pipeline stage 1 / post-mount smoke).
# Runs fully offline in a temp dir against sheet fixtures and a stub gws binary:
# proves query==fixture parity, no-mail-before-confirm, masked approval draft,
# fail-closed confirm, forged/non-owner rejection, (when the W1-6 injection
# adapter is importable) the signed-confirm send path with approval records +
# send log, and the sheet-failure -> retry-queue -> resolve round trip.
set -euo pipefail

fail() { printf 'SCENARIO-FAIL %s\n' "$1" >&2; exit 1; }

secret="${AUTOPHAGY_DEMO_SECRET:-}"
[[ -n "$secret" ]] || fail "AUTOPHAGY_DEMO_SECRET is not set"
[[ "$secret" == DUMMY-* ]] || fail "secret does not carry the DUMMY- prefix (real secrets forbidden in sandbox)"
if [[ "$secret" == *sk-* || "$secret" == *ghp_* || "$secret" == *"Bot "* ]]; then
  fail "secret matches a real-token shape"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"
export BUDGET_DB="$work/budget.db" BUDGET_GATE_DIR="$work/gate"
export BUDGET_APPROVAL_LOG="$work/approvals.jsonl" BUDGET_CONFIG="$work/config.json"
export BUDGET_GWS_BIN="$work/gws-stub"
printf '{"mail_to": "sandbox-selftest@example.invalid"}' > "$work/config.json"
cli() { python3 "$script_dir/budget_cli.py" "$@"; }

fixture() { # fixture <path> <집행액-of-재료비>
  python3 - "$1" "$2" <<'PY'
import json, sys
rows = [["[과제비 운영 규칙]"], ["1. 오너 = 본인"], ["2. 갱신 주기 = 주 1회"],
        ["3. Sheet가 유일한 진실"], [],
        ["항목", "예산", "집행액", "잔액", "최종수정"],
        ["인건비", "0", "0", "0", "2026-07-14"],
        ["재료비", "0", sys.argv[2], "0", "2026-07-14"],
        ["연구활동비", "0", "0", "0", "2026-07-14"]]
json.dump({"majorDimension": "ROWS", "range": "x", "values": rows}, open(sys.argv[1], "w"))
PY
}
fixture "$work/fixture-a.json" "0"
fixture "$work/fixture-b.json" "137137"
python3 - "$work/fixture-broken.json" <<'PY'
import json, sys
rows = [[], [], [], [], [], ["엉뚱", "헤더", "임"], ["인건비", "0", "0", "0", "x"]]
json.dump({"values": rows}, open(sys.argv[1], "w"))
PY

cat > "$work/gws-stub" <<'SH'
#!/bin/sh
{ printf '%s' "$*" | tr '\n' ' '; printf '\n'; } >> "$(dirname "$0")/gws-calls.log"
case "$*" in
  *"gmail +send"*) printf '{"id":"stub-mail-1","status":"sent"}\n' ;;
esac
SH
chmod +x "$work/gws-stub"
calls() { if [ -f "$work/gws-calls.log" ]; then wc -l < "$work/gws-calls.log"; else echo 0; fi; }

# --- 1) !budget query parity against the fixture ------------------------------
export BUDGET_SHEET_FILE="$work/fixture-a.json"
q_out="$(cli query)"
grep -c '^ROW ' <<<"$q_out" | grep -qx 3 || fail "query did not return 3 rows"
grep -q '"항목": "인건비"' <<<"$q_out" || fail "query row missing 인건비"
grep -q '^BUDGET-OK n=3' <<<"$q_out" || fail "query OK marker missing"
cli query --item 재료비 | grep -c '^ROW ' | grep -qx 1 || fail "item filter failed"
set +e
cli query --item 없는항목 >/dev/null 2>&1; nf_rc=$?
set -e
[[ "$nf_rc" -eq 2 ]] || fail "unknown item not exit 2 (rc=$nf_rc)"

# --- 2) baseline + no-change: zero drafts, zero mail ---------------------------
cli snapshot --no-post | grep -q '^BASELINE' || fail "baseline snapshot failed"
cli snapshot --no-post | grep -q '^NO-CHANGE' || fail "unchanged snapshot drafted"
[[ "$(calls)" -eq 0 ]] || fail "read path invoked gws stub"
[[ ! -f "$BUDGET_APPROVAL_LOG" ]] || fail "no-change wrote an approval record"

# --- 3) change -> draft, values masked, still zero mail ------------------------
export BUDGET_SHEET_FILE="$work/fixture-b.json"
d_out="$(cli snapshot --no-post)"
grep -q '^DRAFT-CREATED' <<<"$d_out" || fail "change did not create a draft"
grep -q 'MASKED' <<<"$d_out" || fail "approvals draft is not masked"
grep -q '137137' <<<"$d_out" && fail "raw figure leaked into approvals draft"
draft_id="$(sed -n 's/^DRAFT-CREATED id=\([0-9a-f]*\) .*/\1/p' <<<"$d_out")"
[[ -n "$draft_id" ]] || fail "draft id missing"
cli snapshot --no-post | grep -q '^NO-CHANGE' || fail "post-draft snapshot not advanced"
[[ "$(calls)" -eq 0 ]] || fail "draft stage sent mail (gws called before confirm)"
[[ ! -f "$BUDGET_APPROVAL_LOG" ]] || fail "draft stage wrote an approval record"

# --- 4) confirm without any approval fails closed ------------------------------
if cli confirm --draft "$draft_id" >/dev/null 2>&1; then
  fail "confirm succeeded without owner approval"
fi
[[ "$(calls)" -eq 0 ]] || fail "fail-closed confirm still sent mail"
[[ ! -f "$BUDGET_APPROVAL_LOG" ]] || fail "fail-closed confirm wrote an approval record"

# --- 5) signed injected confirm (only when W1-6 adapter is importable) ---------
confirm_leg="fail-closed-only"
if python3 -I -c '
import os, sys
sys.path.insert(0, os.path.expanduser(os.environ.get("INTEROP_RUNTIME", "~/.hermes/interop_runtime")))
import automation.interop.injection_adapter' 2>/dev/null; then
  export E2E_TEST_MODE=1
  INTEROP_E2E_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  export INTEROP_E2E_SECRET
  export INTEROP_CONFIG="$work/interop-config.json"
  test_owner="999900000000000625"
  # The approvals channel arrives the way production resolves it after AS-3.2:
  # the `personal_approvals_channel_id` config key the shared directory reads.
  printf '{"owner_id": "%s", "personal_approvals_channel_id": "%s"}' \
    "$test_owner" "999000000000000025" > "$INTEROP_CONFIG"

  # Deliberately stamps the PRE-flip v1 guild binding: this leg also proves a
  # record stored before budget moved to the owner DM stays consumable (SI-1).
  # The channel id comes from the REAL resolver (`DiscordChannelDirectory`, config
  # leg — offline, no Discord call) instead of a per-flow env override: AS-3.2
  # retired those, so a scenario that exported one would assert nothing about how
  # production actually resolves a surface.
  persist_e2e_binding() {
    python3 - "$script_dir" "$1" "$test_owner" <<'PY'
import os
import sys

sys.path.insert(0, os.path.expanduser(os.environ.get("INTEROP_RUNTIME", "~/.hermes/interop_runtime")))
sys.path.insert(0, sys.argv[1])
import budget_gate
from automation.interop.approval_directory import DiscordChannelDirectory

channel_id = DiscordChannelDirectory(
    token="sandbox-directory-identity",
    owner_id=sys.argv[3],
).skill_approvals()
draft = budget_gate.load_draft(sys.argv[2])
budget_gate.write_json(
    budget_gate.gate_dir() / "drafts" / f"{draft['id']}.json",
    {
        **draft,
        "kind": "budget-mail",
        "surface": "skill-approvals",
        "channel_id": channel_id,
        "policy_version": 1,
    },
)
PY
  }

  persist_e2e_binding "$draft_id"

  cli sign --draft "$draft_id" --out "$work/forged.json" \
    --user-id "$test_owner" --forge-signature >/dev/null
  if cli confirm --draft "$draft_id" --injection-file "$work/forged.json" >/dev/null 2>&1; then
    fail "forged signature was accepted"
  fi
  cli sign --draft "$draft_id" --out "$work/wrong-owner.json" --user-id "111100000000000111" >/dev/null
  if cli confirm --draft "$draft_id" --injection-file "$work/wrong-owner.json" >/dev/null 2>&1; then
    fail "non-owner approval was accepted"
  fi
  [[ "$(calls)" -eq 0 ]] || fail "rejected confirm still sent mail"
  [[ ! -f "$BUDGET_APPROVAL_LOG" ]] || fail "rejected confirm wrote an approval record"

  cli sign --draft "$draft_id" --out "$work/ok.json" --user-id "$test_owner" >/dev/null
  cli confirm --draft "$draft_id" --injection-file "$work/ok.json" \
    | grep -q "^SENT draft=$draft_id method=signed_injection_e2e" || fail "signed confirm did not send"
  [[ "$(calls)" -eq 1 ]] || fail "expected exactly 1 gws call after confirm"
  grep -q "gmail +send" "$work/gws-calls.log" || fail "executed call is not gmail +send"
  grep -q '"action":"external_effect.approval"' "$BUDGET_APPROVAL_LOG" \
    || fail "external-effect approval record missing"
  grep -q '"action":"budget.request_mail"' "$BUDGET_APPROVAL_LOG" \
    || fail "budget.request_mail audit record missing"
  grep -q '"method":"signed_injection_e2e"' "$BUDGET_APPROVAL_LOG" || fail "approval method missing"
  grep -q '"status":"sent"' "$work/gate/send-log.jsonl" || fail "send log missing"
  grep -q 'sandbox-selftest@example.invalid' "$work/gate/send-log.jsonl" \
    && fail "send log leaked the raw recipient"
  if cli confirm --draft "$draft_id" --injection-file "$work/ok.json" >/dev/null 2>&1; then
    fail "executed draft was confirmable twice"
  fi
  [[ "$(calls)" -eq 1 ]] || fail "double confirm sent a second mail"
  unset E2E_TEST_MODE INTEROP_E2E_SECRET INTEROP_CONFIG
  confirm_leg="signed-confirm"
fi

# --- 6) sheet failure -> error surfaced + retry queued -> resolve --------------
export BUDGET_SHEET_FILE="$work/fixture-broken.json"
set +e
sf_out="$(cli snapshot --no-post 2>&1)"; sf_rc=$?
set -e
[[ "$sf_rc" -eq 4 ]] || fail "broken sheet not exit 4 (rc=$sf_rc)"
grep -q '^SHEET-FAIL retry_queued' <<<"$sf_out" || fail "sheet failure not surfaced"
cli retry-queue | grep -q '^RETRY-QUEUE pending=1' || fail "retry entry not queued"
export BUDGET_SHEET_FILE="$work/fixture-b.json"
cli snapshot --no-post | grep -q '^RETRY-RESOLVED n=1' || fail "retry entry not resolved"
cli retry-queue | grep -q '^RETRY-QUEUE pending=0' || fail "retry queue not drained"

printf 'SCENARIO-PASS leg=%s secret_len=%s account=%s\n' "$confirm_leg" "${#secret}" "$(whoami)"
