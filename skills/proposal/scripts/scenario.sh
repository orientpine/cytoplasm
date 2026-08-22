#!/usr/bin/env bash
set -euo pipefail

case "${AUTOPHAGY_DEMO_SECRET:-}" in
  DUMMY-*) ;;
  *) echo "SCENARIO-REFUSED non-dummy secret" >&2; exit 1 ;;
esac

work="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$work"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

export HOME="$tmp/home"
mkdir -p "$HOME/.local/bin"
export PROPOSAL_WORKSPACE_ROOT="$tmp/agent/proposals"
export PROPOSAL_STATUS_ROOT="$tmp/status"
export PROPOSAL_KANBAN_DISABLED=1
export PROPOSAL_DM_DISABLED=1
cli=(python3 -I "$work/scripts/proposal_cli.py")

"${cli[@]}" create --slug demo-plan --title "Demo plan" \
  --section need:Need --section approach:Approach --section impact:Impact
"${cli[@]}" draft --slug demo-plan --section need --text "Need draft."
"${cli[@]}" draft --slug demo-plan --section approach --text "Approach draft."
"${cli[@]}" contribute --slug demo-plan --section approach --source collaborator --text "Human material."
"${cli[@]}" draft --slug demo-plan --section impact --text "Impact draft."
assembled="$("${cli[@]}" assemble --slug demo-plan)"
grep -Fq 'missing=none' <<<"$assembled"
grep -Fq 'Human material.' "$PROPOSAL_WORKSPACE_ROOT/demo-plan/assembled.md"
! grep -Fq 'Human material.' "$PROPOSAL_STATUS_ROOT/demo-plan.json"
test "$(stat -c '%a' "$PROPOSAL_WORKSPACE_ROOT/demo-plan")" = 700

"${cli[@]}" create --slug incomplete-plan --title "Incomplete plan" \
  --section need:Need --section approach:Approach
"${cli[@]}" draft --slug incomplete-plan --section need --text "Need draft."
missing="$("${cli[@]}" assemble --slug incomplete-plan)"
grep -Fq 'missing=approach' <<<"$missing"
grep -Fq 'ASSEMBLY-REMINDER' <<<"$missing"
grep -Fq '[MISSING SECTION: Approach]' "$PROPOSAL_WORKSPACE_ROOT/incomplete-plan/assembled.md"

cat >"$tmp/knowledge-pack.json" <<'JSON'
{"version":"knowledge-v1","query":{"text":"추진전략 건설 로보틱스 과제","purpose":"synthesize","sources":["rag","wiki","twin"],"tags":[],"limit":8,"caller":"proposal"},"verdict":"hit","items":[{"id":"E1","store":"rag","source_type":"note","ref":"robotics/result.md","title":"실증 결과","doc_date":"2026-08-18","date_basis":"path","score":0.8,"grounded":true,"authority":null,"expired":null,"sensitivity":null,"content":"건설 로보틱스 현장 실증 성과","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}],"layers":{"rag":"hit","wiki":"none","twin":"none"},"notes":[]}
JSON
cat >"$HOME/.local/bin/hermes" <<'SH'
#!/usr/bin/env sh
printf '실증 성과를 다음 단계에 반영한다 [E1].\n'
SH
chmod 700 "$HOME/.local/bin/hermes"
printf '현장 실증을 바탕으로 추진전략을 작성한다.\n' >"$tmp/brief.md"
export KNOWLEDGE_FAKE_PACK="$tmp/knowledge-pack.json"
export LITELLM_AGENT_KEY=DUMMY-PROPOSAL-KEY
export PROPOSAL_LLM_LOG_ROOT="$tmp/logs"
"${cli[@]}" create --slug evidence-plan --title "건설 로보틱스 과제" --section approach:추진전략
"${cli[@]}" draft --slug evidence-plan --section approach --brief-file "$tmp/brief.md" --with-evidence
grep -Fq '실증 성과를 다음 단계에 반영한다 [E1].' "$PROPOSAL_WORKSPACE_ROOT/evidence-plan/sections/01-approach.md"
grep -Fq '[E1] RAG/note: robotics/result.md (2026-08-18, path)' "$PROPOSAL_WORKSPACE_ROOT/evidence-plan/sections/01-approach.md"
test "$(stat -c '%a' "$PROPOSAL_WORKSPACE_ROOT/evidence-plan/sections/01-approach.evidence.json")" = 600
"${cli[@]}" assemble --slug evidence-plan >/dev/null
grep -Fq '## 근거 목록' "$PROPOSAL_WORKSPACE_ROOT/evidence-plan/assembled.md"

echo "SCENARIO-PASS proposal private workspace + missing reminder + offline knowledge pack"
