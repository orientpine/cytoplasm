#!/usr/bin/env bash
set -euo pipefail

fail() { printf 'SCENARIO-FAIL %s\n' "$1" >&2; exit 1; }

secret="${AUTOPHAGY_DEMO_SECRET:-}"
[[ "$secret" == DUMMY-* ]] || fail "dummy secret required"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../../.." && pwd)"
# Probe the facade exactly the way the CLI resolves it (python3 -I + repo_root on
# sys.path). A plain `python3 -c` also imports from the caller's cwd — the node
# sandbox inherits the release root as cwd, so the probe said "hit" while the
# isolated CLI degraded to "unavailable" and `evidence list` failed (2026-08-22).
if python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import automation.knowledge.render' \
  "$repo_root" 2>/dev/null; then
  evidence_pattern='RAG/note: research/flux.md'
  evidence_verdict='hit'
else
  evidence_pattern='근거 수집 불가'
  evidence_verdict='unavailable'
fi
work="$(mktemp -d)"
trap 'cd / && rm -rf "$work"' EXIT
cd "$work"

cat > "$work/rules.yaml" <<'YAML'
tags:
  patent-sensitive:
    keywords:
      - restricted-marker
    patterns: []
YAML
cat > "$work/evidence-pack.json" <<'JSON'
{"version":"knowledge-v1","query":{"text":"autophagy flux","purpose":"synthesize","sources":["rag","wiki","twin"],"tags":[],"limit":8,"caller":"topics"},"verdict":"hit","items":[{"id":"E1","store":"rag","source_type":"note","ref":"research/flux.md","title":"관련 노트","doc_date":"2026-08-18","date_basis":"path","score":0.9,"grounded":true,"authority":null,"expired":null,"sensitivity":null,"content":"flux note","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}],"layers":{"rag":"hit","wiki":"none","twin":"none"},"notes":[]}
JSON

cli() {
  TOPICS_STATE_FILE="$work/research-topics.yaml" TOPICS_RULES_PATH="$work/rules.yaml" \
    AUTOPHAGY_REPO_ROOT="$repo_root" KNOWLEDGE_FAKE_PACK="$work/evidence-pack.json" \
    python3 -I "$script_dir/topics_cli.py" "$@"
}

cli add "autophagy flux" | grep -qx 'TOPIC-ADDED autophagy flux' || fail "safe add"
cli list | grep -qx -- '- autophagy flux' || fail "list"
cli list --with-evidence | grep -q "$evidence_pattern" || fail "evidence list"
grep -q "\"verdict\": \"$evidence_verdict\"" "$work/research-topics.evidence.json" \
  || fail "evidence sidecar"
cli suggest "restricted-marker analysis" | grep -q '^TOPIC-SUGGEST-REFUSED ' || fail "suggest gate"
cli add "restricted-marker analysis" | grep -q '^TOPIC-REFUSED ' || fail "add gate"
grep -q 'restricted-marker' "$work/research-topics.yaml" && fail "sensitive topic persisted"
cli remove "autophagy flux" | grep -qx 'TOPIC-REMOVED' || fail "remove"
cli list | grep -qx 'TOPICS-EMPTY 등록된 주제가 없습니다.' || fail "empty list"

printf 'SCENARIO-PASS legs=add+list+remove+sensitivity account=%s\n' "$(whoami)"
