#!/usr/bin/env bash
# Fully offline sandbox scenario for the todo skill (W1-8 pipeline stage 1).
# No network, no real gws, DUMMY secrets only. Proves the three contracts that
# make Google Tasks writes safe:
#   (a) insert with NO owner approval record is refused by the gate — 0 writes.
#   (b) insert with an approval record runs, then the post-write re-read matches.
#   (c) a re-read whose stored title differs exits non-zero (no silent success).
# The denylist below is a self-contained copy of the repo rule; drift is locked
# by tests/unit/test_todo_skill.py::test_scenario_denylist_fixture_matches_repo_rule.
set -euo pipefail

fail() { printf 'SCENARIO-FAIL %s\n' "$1" >&2; exit 1; }

secret="${AUTOPHAGY_DEMO_SECRET:-}"
[[ -n "$secret" ]] || fail "AUTOPHAGY_DEMO_SECRET is not set"
[[ "$secret" == DUMMY-* ]] || fail "secret does not carry the DUMMY- prefix (real secrets are forbidden in sandbox)"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Only a deploy-supplied root is forced. Inventing one from this script's own location
# would clobber AUTOPHAGY_RUNTIME_ROOT, which is the shared resolver's highest-precedence
# input — and the peer reviewer re-runs this scenario with neither variable set
# (skill_review._scenario_passes gives it HOME/PATH/AUTOPHAGY_DEMO_SECRET only). Staged at
# ~peer/.hermes/skills/todo/scripts, ../../.. resolved to ~peer/.hermes and the resolver
# module vanished, so an owner-approved deploy died at PEER-ATTEST-BLOCK (2026-08-17).
# A developer running this in place still gets their worktree, but only when that tree
# really is a runtime root — never as a blind guess from the script's own depth.
if [[ -n "${AUTOPHAGY_REPO_ROOT:-}" ]]; then
  AUTOPHAGY_RUNTIME_ROOT="$AUTOPHAGY_REPO_ROOT"
  export AUTOPHAGY_RUNTIME_ROOT
elif [[ -z "${AUTOPHAGY_RUNTIME_ROOT:-}" && -d "$script_dir/../../../automation/entity_preflight" ]]; then
  AUTOPHAGY_RUNTIME_ROOT="$(cd "$script_dir/../../.." && pwd)"
  export AUTOPHAGY_RUNTIME_ROOT
fi
AUTOPHAGY_REPO_ROOT="$(python3 "$script_dir/todo_cli.py" runtime-root)" \
  || fail "runtime root diagnostic failed"
export AUTOPHAGY_REPO_ROOT
PYTHONPATH="$AUTOPHAGY_REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH
[[ -d "${AUTOPHAGY_REPO_ROOT:-/nonexistent}/automation/entity_preflight" ]] \
  || fail "AUTOPHAGY_REPO_ROOT has no automation/entity_preflight"

work="$(mktemp -d)"
trap 'cd / && rm -rf "$work"' EXIT

cat > "$work/denylist.yaml" <<'YAML'
rules:
  - id: gws_tasks_mutation
    tool_name_regex: (?i)^(?:terminal|shell|bash|gws)$
    arguments_regex: (?i)(?:^|[^A-Za-z0-9_])gws\s+tasks\s+tasks\s+(?:insert|patch|update|delete|move|clear)(?:\s|$)
YAML

cat > "$work/fake-gws" <<'PY'
#!/usr/bin/env python3
"""In-memory Google Tasks stand-in: records argv, never touches the network."""
import json, os, sys
from pathlib import Path

base = Path(__file__).resolve().parent
argv = sys.argv[1:]
with (base / "gws-calls.log").open("a", encoding="utf-8") as handle:
    handle.write(" ".join(argv[:3]) + "\n")
store = base / "store.json"
if argv[2] == "insert":
    body = json.loads(argv[argv.index("--json") + 1])
    title = body["title"]
    if os.environ.get("FAKE_GWS_MISMATCH") == "1":
        title = title + " (오기입)"
    store.write_text(json.dumps({"id": "task-sandbox-1", "title": title}), encoding="utf-8")
    print(json.dumps({"id": "task-sandbox-1", "title": body["title"]}, ensure_ascii=False))
else:
    print(store.read_text(encoding="utf-8"))
PY
chmod +x "$work/fake-gws"

export TODO_DENYLIST="$work/denylist.yaml"
export TODO_APPROVAL_LOG="$work/approvals.jsonl"
export TODO_APPROVAL_ROOT="$work/todo-approvals"
export TODO_OWNER_ID="owner-sandbox"
export TODO_GWS_BIN="$work/fake-gws"

cli() { python3 "$script_dir/todo_cli.py" "$@"; }

approve() {
  cli plan --title "$1" > "$work/plan.out" || fail "plan failed for $1"
  grep -q 'external_effect=True' "$work/plan.out" || fail "gate did not classify insert as external effect"
  python3 - "$work/plan.out" "$TODO_APPROVAL_LOG" "$TODO_OWNER_ID" "$TODO_APPROVAL_ROOT" "$script_dir" "${2:-normal}" <<'PY' || fail "approval injection"
import json, sys
from datetime import UTC, datetime

sys.path.insert(0, sys.argv[5])
import todo_approval_store as store_module

plan = open(sys.argv[1], encoding="utf-8").read()
fields = dict(part.split("=", 1) for part in plan.split() if "=" in part)
store = store_module.TodoApprovalStore(__import__("pathlib").Path(sys.argv[4]))
spec = store_module.TodoApprovalSpec(
    "todo:" + fields["hash"], fields["hash"], fields["target"],
    "gws tasks tasks insert [masked]", "todo", "owner-dm", "owner-sandbox-dm", 7,
)
if sys.argv[6] == "resume":
    expired = store.bind_message(store.prepare(spec, datetime(2026, 8, 15, tzinfo=UTC)), "sandbox-expired")
    store.archive(expired, store_module.ApprovalState.EXPIRED, None)
pending = store.prepare(spec, datetime(2026, 8, 16, tzinfo=UTC))
message_id = f"sandbox-{pending.generation}"
bound = store.bind_message(pending, message_id)
store.archive(bound, store_module.ApprovalState.ARCHIVED, "approved")
record = {
    "action": "external_effect.approval",
    "approval": {"channel": "approvals", "message_id": message_id, "method": "manual_reaction", "owner_id": sys.argv[3]},
    "hash": fields["hash"],
    "result": {"status": "approved"},
    "target_id": fields["target"],
    "timestamp": "2026-07-28T00:00:00Z",
}
with open(sys.argv[2], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
PY
}

# --- (a) no approval record -> refuse, and prove zero gws calls ---------------
rc=0; cli create --title "무승인 합성 과제" > "$work/blocked.out" 2> "$work/blocked.err" || rc=$?
[[ "$rc" == 4 ]] || fail "unapproved insert exited $rc (expected 4 APPROVAL-REQUIRED)"
grep -q 'TODO-FAIL' "$work/blocked.err" || fail "refusal did not report TODO-FAIL"
[[ ! -e "$work/gws-calls.log" ]] || fail "gws was invoked despite the missing approval record"

# --- (b) approved insert -> write, then matching post-write re-read -----------
approve "승인된 합성 과제"
cli create --title "승인된 합성 과제" > "$work/created.out" || fail "approved insert"
grep -q '^CREATED id=task-sandbox-1 ' "$work/created.out" || fail "create output missing CREATED"
grep -q '^VERIFIED reread=tasks.tasks.get ' "$work/created.out" || fail "post-write re-read not reported"
[[ "$(tr '\n' ' ' < "$work/gws-calls.log")" == "tasks tasks insert tasks tasks get " ]] \
  || fail "expected exactly insert then get, got: $(tr '\n' ' ' < "$work/gws-calls.log")"

rc=0; cli create --title "승인된 합성 과제" > "$work/replay.out" 2> "$work/replay.err" || rc=$?
[[ "$rc" == 4 ]] || fail "consumed approval replay exited $rc"
[[ "$(wc -l < "$work/gws-calls.log")" == 2 ]] || fail "replay invoked gws"

approve "승인된 합성 과제"
cli create --title "승인된 합성 과제" > "$work/created-again.out" || fail "second generation insert"
[[ "$(wc -l < "$work/gws-calls.log")" == 4 ]] || fail "second generation did not execute exactly once"

rc=0; cli create --title "해시 불일치 과제" > "$work/hash.out" 2> "$work/hash.err" || rc=$?
[[ "$rc" == 4 ]] || fail "hash mismatch exited $rc"
[[ "$(wc -l < "$work/gws-calls.log")" == 4 ]] || fail "hash mismatch invoked gws"

# --- (c) re-read mismatch -> non-zero exit, never a silent success ------------
approve "재조회 불일치 합성 과제"
rc=0; FAKE_GWS_MISMATCH=1 cli create --title "재조회 불일치 합성 과제" > "$work/mismatch.out" 2> "$work/mismatch.err" || rc=$?
[[ "$rc" == 6 ]] || fail "re-read mismatch exited $rc (expected 6 VERIFY-FAIL)"
grep -q 'TODO-FAIL' "$work/mismatch.err" || fail "mismatch did not report TODO-FAIL"
grep -q 'VERIFIED' "$work/mismatch.out" && fail "mismatch still claimed verification"

approve "만료 후 재개 과제" resume
cli create --title "만료 후 재개 과제" > "$work/resumed.out" || fail "expiry resume insert"
grep -q '^VERIFIED reread=tasks.tasks.get ' "$work/resumed.out" || fail "expiry resume lacked verification"

# --- reads stay ungated ------------------------------------------------------
cli plan --title "무승인 합성 과제" | grep -q 'approved=False' || fail "plan lost its refusal reporting"

printf 'BLOCKED rc=4 gws_calls=0\n'
grep '^CREATED ' "$work/created.out"
grep '^VERIFIED ' "$work/created.out"
printf 'MISMATCH rc=6 verified=false\n'
printf 'APPROVAL-CYCLE-PASS happy=1 failures=4 resume=1 full_cycle=1\n'
printf 'SCENARIO-PASS todo offline (gate-block/insert+reread-match/reread-mismatch-nonzero)\n'
