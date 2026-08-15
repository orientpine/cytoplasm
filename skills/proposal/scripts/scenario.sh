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

echo "SCENARIO-PASS proposal private workspace + missing reminder"
