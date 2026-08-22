#!/usr/bin/env bash
# Sandbox scenario for the wiki skill (W1-8 pipeline stage 1 / post-mount smoke).
# Runs fully offline in a temp dir: proves no-write-before-confirm, fail-closed
# confirm, schema rejection with guidance, and (when the W1-6 injection adapter
# is importable) the signed-confirm save path plus the DT-B6 twin reaction legs:
# twin draft saved via injected owner ✅ reaction, and ✅+⛔ both injected →
# ⛔-precedence discard. Unconditional twin legs: bad-enum SCHEMA-REJECTED and
# expired-review_after REVIEW-EXPIRED, and a twin_consult read-only proof
# (conflict / expired-demotion / none verdicts asserted on the ranked JSON;
# the fixture vault must be byte-identical before and after every consult).
# No network calls, DUMMY secret only.
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
cd "$work"  # deploy runs this as peer from an inaccessible CWD (<operator-account> home); GNU find exits 1 restoring CWD otherwise
export WIKI_ROOT="$work/wiki" WIKI_GATE_DIR="$work/gate"
cli() { python3 "$script_dir/wiki_cli.py" "$@"; }

# Sign a DT-B0 reaction-injection envelope (✅/⛔) bound to a pending draft.
sign_reaction() { # <draft-id> <out-file> <emoji>
  python3 - "$script_dir" "$1" "$2" "$3" <<'PY'
import sys
from pathlib import Path

script_dir, draft_id, out, emoji = sys.argv[1:5]
sys.path.insert(0, script_dir)
import wiki_gate

wiki_gate.sign_injection(
    wiki_gate.load_draft(draft_id), Path(out), None, None, False, reaction_emoji=emoji
)
PY
}

# --- 1) draft writes NOTHING into the wiki root -----------------------------
out="$(cli draft --title "게이트 테스트 노트" --tags "test,w2-2" \
  --body "확인 게이트 검증용 본문." --channel-id 999000000000000031)"
draft_id="$(printf '%s\n' "$out" | sed -n 's/^DRAFT-CREATED id=\([0-9a-f]*\) .*/\1/p')"
[[ -n "$draft_id" ]] || fail "draft id missing from output"
[[ -z "$(find "$WIKI_ROOT" -type f 2>/dev/null)" ]] || fail "draft stage wrote into WIKI_ROOT"

# --- 2) confirm without any confirmation fails closed -----------------------
if cli confirm --draft "$draft_id" >/dev/null 2>&1; then
  fail "confirm succeeded without owner confirmation"
fi
[[ -z "$(find "$WIKI_ROOT" -type f 2>/dev/null)" ]] || fail "fail-closed confirm still wrote a file"

# --- 3) schema-violating frontmatter is rejected with guidance --------------
set +e
bad_out="$(cli draft --title "" --tags "ok" --body "x" --channel-id 999000000000000031 2>&1)"
bad_rc=$?
set -e
[[ "$bad_rc" -eq 2 ]] || fail "schema violation not rejected with exit 2 (rc=$bad_rc)"
printf '%s' "$bad_out" | grep -q "SCHEMA-REJECTED" || fail "rejection lacks SCHEMA-REJECTED marker"
printf '%s' "$bad_out" | grep -q "frontmatter 스키마 안내" || fail "rejection lacks schema guidance"

printf '%s\n' '---' 'title: "bad"' 'oops: 1' '---' 'body' > "$work/bad-note.md"
if cli validate --file "$work/bad-note.md" >/dev/null 2>&1; then
  fail "validate accepted an unknown frontmatter key"
fi

# --- 4) signed injected confirm + twin reaction legs (W1-6 adapter needed) --
confirm_leg="fail-closed-only"
twin_leg="skipped"
if python3 -I -c '
import os, sys
sys.path.insert(0, os.path.expanduser(os.environ.get("INTEROP_RUNTIME", "~/.hermes/interop_runtime")))
import automation.interop.injection_adapter' 2>/dev/null; then
  export E2E_TEST_MODE=1
  INTEROP_E2E_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  export INTEROP_E2E_SECRET
  export INTEROP_CONFIG="$work/interop-config.json"
  test_owner="999900000000000625"
  printf '{"owner_id": "%s"}' "$test_owner" > "$INTEROP_CONFIG"

  persist_e2e_binding() {
    python3 - "$script_dir" "$1" <<'PY'
import os
import sys

sys.path.insert(0, sys.argv[1])
import wiki_gate

draft = wiki_gate.load_draft(sys.argv[2])
wiki_gate._write_json(
    wiki_gate._draft_path(draft["id"]),
    {
        **draft,
        "kind": "wiki",
        "surface": "owner-dm",
        "channel_id": os.environ["SCENARIO_APPROVAL_CHANNEL_ID"],
        "policy_version": 1,
    },
)
PY
  }

  export SCENARIO_APPROVAL_CHANNEL_ID="999000000000000031"
  persist_e2e_binding "$draft_id"

  cli sign --draft "$draft_id" --out "$work/forged.json" \
    --user-id "$test_owner" --channel-id 999000000000000031 --forge-signature >/dev/null
  if cli confirm --draft "$draft_id" --injection-file "$work/forged.json" >/dev/null 2>&1; then
    fail "forged signature was accepted"
  fi
  [[ -z "$(find "$WIKI_ROOT" -type f 2>/dev/null)" ]] || fail "forged confirm wrote a file"

  cli sign --draft "$draft_id" --out "$work/ok.json" \
    --user-id "$test_owner" --channel-id 999000000000000031 >/dev/null
  cli confirm --draft "$draft_id" --injection-file "$work/ok.json" \
    | grep -q '^SAVED ' || fail "signed confirm did not save"
  saved_note="$WIKI_ROOT/게이트-테스트-노트.md"
  [[ -f "$saved_note" ]] || fail "note file absent after confirmed save"
  cli validate --file "$saved_note" >/dev/null || fail "saved note failed schema validation"
  confirm_leg="signed-confirm"

  # --- 4b) twin draft saved via injected owner ✅ reaction (DT-B0 mode) -----
  twin_body="$(cat <<'MD'
## Context
샌드박스 검증용 결정 배경.

## Decision
리액션 게이트로 저장한다.

## Rationale & Trade-offs
오프라인 서명 주입으로 게이트를 검증한다.

## What would change my mind
게이트 규약이 바뀌면.
MD
)"
  twin_out="$(cli draft --title "트윈 결정 노트" --slug twin-decision-note \
    --tags "twin,decision" --channel-id 999000000000000031 \
    --kind decision --authority default --provenance stated \
    --review-after 2099-12-31 --body "$twin_body")"
  twin_id="$(printf '%s\n' "$twin_out" | sed -n 's/^DRAFT-CREATED id=\([0-9a-f]*\) .*/\1/p')"
  [[ -n "$twin_id" ]] || fail "twin draft id missing from output"
  persist_e2e_binding "$twin_id"
  if printf '%s' "$twin_out" | grep -q 'TEMPLATE-WARN'; then
    fail "complete decision template unexpectedly warned"
  fi
  sign_reaction "$twin_id" "$work/twin-approve.json" "✅"
  cli confirm --draft "$twin_id" --injection-file "$work/twin-approve.json" \
    | grep -q '^SAVED ' || fail "twin ✅ reaction injection did not save"
  twin_note="$WIKI_ROOT/twin-decision-note.md"
  [[ -f "$twin_note" ]] || fail "twin note absent after ✅ reaction save"
  grep -q '^kind: decision$' "$twin_note" || fail "saved twin note lacks kind"
  grep -q '^authority: default$' "$twin_note" || fail "saved twin note lacks authority"
  grep -q '^provenance: stated$' "$twin_note" || fail "saved twin note lacks provenance"
  grep -q '^review_after: 2099-12-31$' "$twin_note" || fail "saved twin note lacks review_after"
  cli validate --file "$twin_note" >/dev/null || fail "saved twin note failed schema validation"

  # --- 4b2) stated principle (DT-C1) → owner ✅ reaction saves provenance:stated -
  princ_out="$(cli draft --title "트윈 원칙 노트" --slug twin-principle-note \
    --tags "twin,principle" --channel-id 999000000000000031 \
    --kind principle --authority default --provenance stated \
    --body "$(printf '## Trigger\n호의를 베푼 상대와 좋은 관계일 때.\n\n## Rule\n합리적 범위에서 배려한다.\n\n## Exceptions\n기관 규정·공정성·보안·cha 이해관계 우선.')")"
  princ_id="$(printf '%s\n' "$princ_out" | sed -n 's/^DRAFT-CREATED id=\([0-9a-f]*\) .*/\1/p')"
  [[ -n "$princ_id" ]] || fail "stated principle draft id missing from output"
  persist_e2e_binding "$princ_id"
  sign_reaction "$princ_id" "$work/princ-approve.json" "✅"
  cli confirm --draft "$princ_id" --injection-file "$work/princ-approve.json" \
    | grep -q '^SAVED ' || fail "stated principle ✅ reaction injection did not save"
  princ_note="$WIKI_ROOT/twin-principle-note.md"
  [[ -f "$princ_note" ]] || fail "stated principle note absent after ✅ reaction save"
  grep -q '^kind: principle$' "$princ_note" || fail "stated principle note lacks kind"
  grep -q '^provenance: stated$' "$princ_note" || fail "stated principle note lacks provenance:stated"
  grep -q '^authority: default$' "$princ_note" || fail "stated principle note lacks authority"

  # --- 4c) ✅+⛔ both injected → ⛔ precedence: cancel, discard, no revival --
  twin2_out="$(cli draft --title "트윈 취소 노트" --slug twin-cancel-note \
    --tags "twin" --channel-id 999000000000000031 \
    --kind preference --authority advisory --provenance stated \
    --body "$(printf '## Preference\n샌드박스 취소 검증.\n\n## Boundary\n저장되면 안 된다.')")"
  twin2_id="$(printf '%s\n' "$twin2_out" | sed -n 's/^DRAFT-CREATED id=\([0-9a-f]*\) .*/\1/p')"
  [[ -n "$twin2_id" ]] || fail "second twin draft id missing from output"
  persist_e2e_binding "$twin2_id"
  # Both owner decisions exist BEFORE any resolution; the gate resolves ⛔
  # FIRST — the same cancel-before-approve precedence resolve_reaction applies
  # (the single-message ✅+⛔ dual-reaction case is unit-locked in
  # tests/unit/test_wiki_gate_reaction.py).
  sign_reaction "$twin2_id" "$work/twin2-approve.json" "✅"
  sign_reaction "$twin2_id" "$work/twin2-cancel.json" "⛔"
  set +e
  cancel_out="$(cli confirm --draft "$twin2_id" --injection-file "$work/twin2-cancel.json" 2>&1)"
  cancel_rc=$?
  set -e
  [[ "$cancel_rc" -eq 1 ]] || fail "⛔ injection did not cancel with exit 1 (rc=$cancel_rc)"
  printf '%s' "$cancel_out" | grep -q '취소' || fail "⛔ cancel refusal message missing"
  [[ ! -f "$WIKI_ROOT/twin-cancel-note.md" ]] || fail "⛔-cancelled draft wrote a note"
  cli discard --draft "$twin2_id" >/dev/null  # mirror resolve_reaction: ⛔ discards the draft
  if cli confirm --draft "$twin2_id" --injection-file "$work/twin2-approve.json" >/dev/null 2>&1; then
    fail "leftover ✅ envelope resurrected a ⛔-discarded draft"
  fi
  [[ ! -f "$WIKI_ROOT/twin-cancel-note.md" ]] || fail "discarded twin draft still saved a note"
  twin_leg="reaction-approve+cancel-precedence"

  unset E2E_TEST_MODE INTEROP_E2E_SECRET INTEROP_CONFIG
fi

# --- 5) read-only query/backlinks/cleanup on seeded fixtures ----------------
mkdir -p "$WIKI_ROOT" && chmod 700 "$WIKI_ROOT"
cat > "$WIKI_ROOT/alpha-note.md" <<'MD'
---
title: "Alpha note"
tags: [seed]
created: 2026-01-01T00:00:00Z
updated: 2026-01-01T00:00:00Z
links: [beta-note]
---
alpha body mentions QUERYTOKEN
MD
cat > "$WIKI_ROOT/beta-note.md" <<'MD'
---
title: "Beta note"
tags: []
created: 2026-01-01T00:00:00Z
updated: 2026-01-01T00:00:00Z
links: []
---
beta body
MD
cli query QUERYTOKEN | grep -q 'slug=alpha-note' || fail "query round-trip missed alpha-note"
cli query nothing-matches-this | grep -q 'hits=0' || fail "query false positive"
cli backlinks beta-note | grep -q 'slug=alpha-note' || fail "backlinks missed alpha-note -> beta-note"
cleanup_out="$(cli cleanup-suggest)"
printf '%s' "$cleanup_out" | grep -q 'STALE alpha-note' || fail "cleanup: stale suggestion missing"
printf '%s' "$cleanup_out" | grep -q 'UNTAGGED beta-note' || fail "cleanup: untagged suggestion missing"

# --- 6) twin bad-enum draft → exit 2 SCHEMA-REJECTED, nothing written -------
drafts_before="$(find "$WIKI_GATE_DIR/drafts" -type f 2>/dev/null | wc -l)"
notes_before="$(find "$WIKI_ROOT" -type f 2>/dev/null | wc -l)"
set +e
twin_bad="$(cli draft --title "트윈 배드 에넘" --slug twin-bad-enum --tags "twin" \
  --channel-id 999000000000000031 --kind decision --authority binding --provenance stated \
  --body "x" 2>&1)"
twin_bad_rc=$?
set -e
[[ "$twin_bad_rc" -eq 2 ]] || fail "bad twin enum not rejected with exit 2 (rc=$twin_bad_rc)"
printf '%s' "$twin_bad" | grep -q "SCHEMA-REJECTED" || fail "twin enum rejection lacks SCHEMA-REJECTED marker"
printf '%s' "$twin_bad" | grep -q "authority" || fail "twin enum rejection lacks authority guidance"
[[ "$(find "$WIKI_GATE_DIR/drafts" -type f 2>/dev/null | wc -l)" -eq "$drafts_before" ]] \
  || fail "rejected twin draft left a draft record"
[[ "$(find "$WIKI_ROOT" -type f 2>/dev/null | wc -l)" -eq "$notes_before" ]] \
  || fail "rejected twin draft wrote into WIKI_ROOT"
[[ ! -f "$WIKI_ROOT/twin-bad-enum.md" ]] || fail "rejected twin draft saved a note"

# --- 7) expired review_after fixture → REVIEW-EXPIRED cleanup suggestion ----
cat > "$WIKI_ROOT/gamma-decision.md" <<'MD'
---
title: "Gamma decision"
tags: [seed, twin]
created: 2026-01-01T00:00:00Z
updated: 2026-01-01T00:00:00Z
links: [alpha-note]
kind: decision
authority: default
provenance: stated
review_after: 2020-01-02
---
## Context
seed

## Decision
seed

## Rationale & Trade-offs
seed

## What would change my mind
seed
MD
cli validate --file "$WIKI_ROOT/gamma-decision.md" >/dev/null || fail "gamma twin fixture failed validation"
cli cleanup-suggest | grep -q 'REVIEW-EXPIRED gamma-decision' \
  || fail "cleanup: REVIEW-EXPIRED suggestion missing"

# --- 8) twin_consult read-only proof: ranked verdicts, vault byte-identical -
consult_vault="$work/consult-vault"
mkdir -p "$consult_vault" && chmod 700 "$consult_vault"
cat > "$consult_vault/consult-stated-default.md" <<'MD'
---
title: "Consult stated default"
tags: [consult-budget, twin]
created: 2026-01-01T00:00:00Z
updated: 2026-02-01T00:00:00Z
links: []
kind: principle
authority: default
provenance: stated
---
## Trigger
seed

## Rule
seed

## Exceptions
seed
MD
cat > "$consult_vault/consult-observed-advisory.md" <<'MD'
---
title: "Consult observed advisory"
tags: [consult-budget]
created: 2026-01-01T00:00:00Z
updated: 2026-03-01T00:00:00Z
links: []
kind: principle
authority: advisory
provenance: observed
---
## Trigger
seed

## Rule
seed

## Exceptions
seed
MD
cat > "$consult_vault/consult-expired-strict.md" <<'MD'
---
title: "Consult expired strict"
tags: [consult-deploy]
created: 2026-01-01T00:00:00Z
updated: 2026-01-15T00:00:00Z
links: []
kind: decision
authority: strict
provenance: stated
review_after: 2020-01-01
---
## Context
seed

## Decision
seed

## Rationale & Trade-offs
seed

## What would change my mind
seed
MD
for fixture in consult-stated-default consult-observed-advisory consult-expired-strict; do
  cli validate --file "$consult_vault/$fixture.md" >/dev/null \
    || fail "consult fixture $fixture failed schema validation"
done

# sha256 of EVERY vault file + the full directory listing — the consult legs
# below must leave this byte-identical (SI-2 read-only guarantee).
vault_snapshot() {
  (cd "$1" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0r sha256sum; find . -print | LC_ALL=C sort)
}
consult() { python3 "$script_dir/twin_consult.py" --root "$consult_vault" "$@"; }

before_state="$(vault_snapshot "$consult_vault")" || fail "consult vault pre-snapshot failed"
[[ -n "$before_state" ]] || fail "consult vault pre-snapshot is empty (fixtures missing)"

conflict_json="$(consult --tags consult-budget --json)" \
  || fail "twin_consult conflict case exited non-zero"
expired_json="$(consult --tags consult-deploy --kinds decision --json)" \
  || fail "twin_consult expired-strict case exited non-zero"
none_json="$(consult --tags consult-unmatched --json)" \
  || fail "twin_consult no-match case exited non-zero"

python3 - "$conflict_json" "$expired_json" "$none_json" <<'PY' \
  || fail "twin_consult ranked-JSON/verdict assertions failed"
import json
import sys

conflict, expired, none_hit = (json.loads(raw) for raw in sys.argv[1:4])

assert conflict["verdict"] == "conflict", conflict
slugs = [rule["slug"] for rule in conflict["rules"]]
assert slugs == ["consult-stated-default", "consult-observed-advisory"], slugs

assert expired["verdict"] == "ok", expired
(rule,) = expired["rules"]
assert rule["slug"] == "consult-expired-strict", rule
assert rule["expired"] is True, rule
assert rule["authority_declared"] == "strict", rule
assert rule["authority"] == "default", rule  # demoted one level before ranking

assert none_hit["verdict"] == "none" and none_hit["rules"] == [], none_hit
PY

after_state="$(vault_snapshot "$consult_vault")" || fail "consult vault post-snapshot failed"
[[ "$before_state" == "$after_state" ]] \
  || fail "twin_consult mutated the fixture vault (read-only violation)"
consult_leg="read-only-verdicts"

# --- 9) adopted consult: one offline facade pack, labeled output + draft traceability
if [[ -d "${INTEROP_RUNTIME:-}/automation/knowledge" ]]; then
  export AUTOPHAGY_REPO_ROOT="$INTEROP_RUNTIME"
fi
cat > "$work/knowledge-pack.json" <<'JSON'
{"version":"knowledge-v1","query":{"text":"consult-budget 판단","purpose":"judgment","sources":["rag","wiki","twin"],"tags":["consult-budget"],"limit":8,"caller":"wiki"},"verdict":"hit","items":[{"id":"E1","store":"wiki","source_type":"twin","ref":"consult-stated-default","title":"Consult stated default","doc_date":"2026-02-01","date_basis":"updated","score":null,"grounded":null,"authority":"default","expired":false,"sensitivity":null,"content":"승인된 예산 원칙","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},{"id":"E2","store":"rag","source_type":"conversation","ref":"conversation/42","title":"Budget precedent","doc_date":"2026-01-20","date_basis":"day","score":0.8,"grounded":true,"authority":null,"expired":null,"sensitivity":null,"content":"과거 예산 선례","sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}],"layers":{"rag":"hit","wiki":"hit","twin":"ok"},"notes":[]}
JSON
export KNOWLEDGE_FAKE_PACK="$work/knowledge-pack.json"
facade_out="$(cli consult 'consult-budget 판단')" || fail "facade consult exited non-zero"
printf '%s' "$facade_out" | grep -Fq '[위키 규칙] [E1] wiki:' || fail "facade wiki label"
printf '%s' "$facade_out" | grep -Fq '[RAG 선례] [E2] RAG/대화:' || fail "facade RAG label"
facade_draft="$(cli draft --title '근거 대화 요약' --slug evidence-summary --tags consult-budget \
  --body '대화 판단 [E1].' --with-evidence)" || fail "evidence draft exited non-zero"
facade_id="$(printf '%s\n' "$facade_draft" | sed -n 's/^DRAFT-CREATED id=\([0-9a-f]*\) .*/\1/p')"
[[ -f "$WIKI_GATE_DIR/evidence/$facade_id.evidence.json" ]] || fail "evidence sidecar absent"
[[ "$(stat -c '%a' "$WIKI_GATE_DIR/evidence/$facade_id.evidence.json")" = 600 ]] \
  || fail "evidence sidecar mode"
[[ ! -f "$WIKI_ROOT/evidence-summary.md" ]] || fail "evidence draft bypassed write gate"
facade_leg="single-call+sources"

printf 'SCENARIO-PASS leg=%s twin=%s consult=%s facade=%s secret_len=%s account=%s\n' \
  "$confirm_leg" "$twin_leg" "$consult_leg" "$facade_leg" "${#secret}" "$(whoami)"
