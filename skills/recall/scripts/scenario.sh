#!/usr/bin/env bash
# Sandbox scenario for the recall skill (W1-8 pipeline stage 1 / post-mount smoke).
# Fully offline: fixture rows exercise the recall-v1 schema, threshold+grounding
# classification, BOTH sensitivity branches of the v2 model-route guard
# (GLM route -> exclusion / non-GLM route -> sentinel release), the no-result
# "기억 없음" contract, and the RAG-down "unavailable" fallback (exactly 1
# attempt, no retry). RECALL_HERMES_CONFIG is pinned to scenario-owned files so
# the run is deterministic on any account — both sandbox and post-mount smoke run
# with a disposable HOME, so the live hermes config cannot flip these branches.
# No network, no secrets.
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
trap 'cd / && rm -rf "$work"' EXIT
cd "$work"  # sandbox cwd may be unreadable to this account (breaks find/py cwd)
export RECALL_LOG_DIR="$work/logs"
cli() { python3 "$script_dir/recall_cli.py" "$@"; }

# --- fixtures ----------------------------------------------------------------
cat > "$work/rows.json" <<'JSON'
[
  {"score": 0.61, "source": "wiki:w2-5-테스트-노트.md#c0000",
   "content": "차세대 배양기 코드네임은 pistachio-5501이다.",
   "document_id": "00000000-0000-0000-0000-000000000001",
   "metadata": {"source_type": "wiki", "title": "W2-5 테스트 노트",
                "path": "w2-5-테스트-노트.md", "agent_id": "agent",
                "role": "personal-research-agent", "project": "autophagy",
                "interest_tags": "autophagy,rag"}},
  {"score": 0.52, "source": "meeting:2026-07-15-회의.md#c0000",
   "content": "배양기 코드네임 pistachio-5501 논의 회의.",
   "document_id": "00000000-0000-0000-0000-000000000002",
   "metadata": {"source_type": "meeting", "title": "회의",
                "path": "2026-07-15-회의.md"}},
  {"score": 0.30, "source": "wiki:노이즈.md#c0000", "content": "무관한 내용.",
   "document_id": "00000000-0000-0000-0000-000000000003",
   "metadata": {"source_type": "wiki", "path": "노이즈.md"}},
  {"score": 0.61, "source": "wiki:민감-검색.md#c0000",
   "content": "배양기 코드네임의 민감 분류 원문.",
   "document_id": "00000000-0000-0000-0000-000000000004",
   "metadata": {"source_type": "wiki", "path": "민감-검색.md",
                "sensitivity": "patent-sensitive"}}
]
JSON

# --- model-route pins (v2 sensitivity guard input, never the live config) ----
cat > "$work/hermes-glm.yaml" <<'YAML'
model:
  default: glm-main
  provider: custom:litellm
YAML
cat > "$work/hermes-sol.yaml" <<'YAML'
model:
  default: gpt-5.6-sol
  provider: openai-codex
YAML
export RECALL_HERMES_CONFIG="$work/hermes-glm.yaml"

# --- 1) hit: schema + attribution + threshold floor --------------------------
RECALL_FAKE_RESULTS="$work/rows.json" cli search "pistachio-5501 배양기 코드네임" --json \
  > "$work/hit.out" 2> "$work/hit-summary.out"
python3 - "$work/hit.out" <<'PY' || fail "hit schema/attribution assertions failed"
import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
assert r["version"] == "recall-v1", "version"
assert r["status"] == "hit", f"status={r['status']}"
assert r["message"] is None, "hit must carry no message"
assert set(r) == {"version", "query", "status", "message", "threshold",
                  "strong_threshold", "results", "search"}, "top-level keys"
assert len(r["results"]) == 2, "0.30 row must be dropped by the 0.45 floor"
assert all(item["source"] != "wiki:민감-검색.md#c0000" for item in r["results"])
assert "민감 분류 원문" not in json.dumps(r, ensure_ascii=False)
first = r["results"][0]
assert set(first) == {"rank", "score", "grounded", "source", "source_type",
                      "attribution", "title", "excerpt", "metadata"}, "result keys"
assert first["rank"] == 1 and first["score"] == 0.61
assert first["attribution"] == "위키: w2-5-테스트-노트.md", first["attribution"]
assert first["grounded"] is True
assert r["results"][1]["attribution"] == "회의: 2026-07-15-회의.md"
assert r["search"]["attempts"] == 1
PY
grep -Fxq '1건은 민감 분류로 제외' "$work/hit-summary.out" \
  || fail "sensitive exclusion summary is missing or unmasked"

# --- 1b) same rows, non-GLM primary route -> sensitive row released + sentinel
RECALL_FAKE_RESULTS="$work/rows.json" RECALL_HERMES_CONFIG="$work/hermes-sol.yaml" \
  cli search "pistachio-5501 배양기 코드네임" --json \
  > "$work/hit-sol.out" 2> "$work/hit-sol-summary.out"
python3 - "$work/hit-sol.out" <<'PY' || fail "non-GLM sentinel release assertions failed"
import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
assert r["status"] == "hit", f"status={r['status']}"
assert len(r["results"]) == 3, "sensitive row must be released on a non-GLM route"
sensitive = [x for x in r["results"] if x["source"] == "wiki:민감-검색.md#c0000"]
assert len(sensitive) == 1, "released sensitive row missing"
assert sensitive[0]["excerpt"].startswith("[[PATENT-SENSITIVE-RECALL]]"), "sentinel marker missing"
others = [x for x in r["results"] if x["source"] != "wiki:민감-검색.md#c0000"]
assert all("[[PATENT-SENSITIVE-RECALL]]" not in x["excerpt"] for x in others), "marker leaked to non-sensitive rows"
assert len([x for x in r["results"] if x["score"] >= 0.45]) == 3, "0.30 row must still be dropped by the floor"
PY
grep -q '1건 patent-sensitive 포함' "$work/hit-sol-summary.out" \
  || fail "sentinel release notice missing"
grep -q '민감 분류로 제외' "$work/hit-sol-summary.out" \
  && fail "exclusion summary must not appear on the release branch" || true

# --- 2) fabricated-token query against the same rows -> 기억 없음 -------------
# (score-only threshold would pass 0.52; grounding must reject it)
cat > "$work/lookalike.json" <<'JSON'
[{"score": 0.52, "source": "wiki:w2-5-테스트-노트.md#c0000",
  "content": "차세대 배양기 코드네임은 pistachio-5501이다.",
  "document_id": "00000000-0000-0000-0000-000000000001",
  "metadata": {"source_type": "wiki", "path": "w2-5-테스트-노트.md"}}]
JSON
RECALL_FAKE_RESULTS="$work/lookalike.json" cli search "zephyrine-88231 원심분리기 제조사" --json > "$work/nomem.out"
python3 - "$work/nomem.out" <<'PY' || fail "no_memory assertions failed"
import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
assert r["status"] == "no_memory", f"status={r['status']}"
assert r["message"] == "기억 없음", r["message"]
assert r["results"] == [], "no_memory must carry zero results"
PY
RECALL_FAKE_RESULTS="$work/lookalike.json" cli search "zephyrine-88231 원심분리기 제조사" \
  | grep -q '^RECALL-NO-MEMORY 기억 없음' || fail "text rendering lacks 기억 없음"

# --- 3) RAG down -> unavailable, exactly 1 attempt, exit 0 -------------------
RECALL_FAKE_ERROR=unreachable cli search "아무 질문" --json > "$work/down.out" \
  || fail "unavailable path must still exit 0 (agent falls back to general answer)"
python3 - "$work/down.out" <<'PY' || fail "unavailable assertions failed"
import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
assert r["status"] == "unavailable", f"status={r['status']}"
assert "검색 불가" in r["message"], r["message"]
assert r["results"] == []
assert r["search"]["attempts"] == 1, "must be a single attempt (no retry)"
assert r["search"]["error"], "masked error reason expected"
PY
RECALL_FAKE_ERROR=unreachable cli search "아무 질문" \
  | grep -q '^RECALL-UNAVAILABLE' || fail "text rendering lacks RECALL-UNAVAILABLE"

# --- 4) search log written, one masked JSON line per call, mode 600 ----------
log_file="$(find "$RECALL_LOG_DIR" -name 'recall-*.log' -type f | head -n1)"
[[ -n "$log_file" ]] || fail "recall log file absent"
[[ "$(wc -l < "$log_file")" -eq 6 ]] || fail "expected 6 log lines (one per search)"
[[ "$(stat -c %a "$log_file")" == "600" ]] || fail "recall log is not mode 600"
grep -q '"status": "no_memory"' "$log_file" || fail "log lacks no_memory record"
grep -q '"status": "unavailable"' "$log_file" || fail "log lacks unavailable record"

printf 'SCENARIO-PASS legs=hit+sentinel_release+no_memory+unavailable secret_len=%s account=%s\n' \
  "${#secret}" "$(whoami)"
