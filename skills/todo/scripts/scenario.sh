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
# The peer attestation stages this skill outside the repo tree (~peer/.hermes/skills/todo),
# where $script_dir/../../.. has no automation package. Resolve AUTOPHAGY_REPO_ROOT to a
# checkout that actually carries automation/ (and entity_preflight), preferring an explicit
# override, then the script-relative repo, then the node's ops checkout.
if [[ -z "${AUTOPHAGY_REPO_ROOT:-}" ]]; then
  for candidate in "$(cd "$script_dir/../../.." && pwd)" /srv/autophagy-agents; do
    if [[ -d "$candidate/automation/interop" && -d "$candidate/automation/entity_preflight" ]]; then
      AUTOPHAGY_REPO_ROOT="$candidate"; break
    fi
  done
fi
export AUTOPHAGY_REPO_ROOT
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
export TODO_OWNER_ID="owner-sandbox"
export TODO_GWS_BIN="$work/fake-gws"

cli() { python3 "$script_dir/todo_cli.py" "$@"; }

approve() { # approve <title> — append an owner record bound to that exact argv
  cli plan --title "$1" > "$work/plan.out" || fail "plan failed for $1"
  grep -q 'external_effect=True' "$work/plan.out" || fail "gate did not classify insert as external effect"
  python3 - "$work/plan.out" "$TODO_APPROVAL_LOG" "$TODO_OWNER_ID" <<'PY' || fail "approval injection"
import json, sys

plan = open(sys.argv[1], encoding="utf-8").read()
fields = dict(part.split("=", 1) for part in plan.split() if "=" in part)
record = {
    "action": "external_effect.approval",
    "approval": {"channel": "approvals", "message_id": "sandbox", "method": "manual_reaction", "owner_id": sys.argv[3]},
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

# --- (c) re-read mismatch -> non-zero exit, never a silent success ------------
approve "재조회 불일치 합성 과제"
rc=0; FAKE_GWS_MISMATCH=1 cli create --title "재조회 불일치 합성 과제" > "$work/mismatch.out" 2> "$work/mismatch.err" || rc=$?
[[ "$rc" == 6 ]] || fail "re-read mismatch exited $rc (expected 6 VERIFY-FAIL)"
grep -q 'TODO-FAIL' "$work/mismatch.err" || fail "mismatch did not report TODO-FAIL"
grep -q 'VERIFIED' "$work/mismatch.out" && fail "mismatch still claimed verification"

# --- reads stay ungated ------------------------------------------------------
cli plan --title "무승인 합성 과제" | grep -q 'approved=False' || fail "plan lost its refusal reporting"

printf 'BLOCKED rc=4 gws_calls=0\n'
grep '^CREATED ' "$work/created.out"
grep '^VERIFIED ' "$work/created.out"
printf 'MISMATCH rc=6 verified=false\n'
printf 'SCENARIO-PASS todo offline (gate-block/insert+reread-match/reread-mismatch-nonzero)\n'
