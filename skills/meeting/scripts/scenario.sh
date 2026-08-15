#!/usr/bin/env bash
# W2-3 meeting skill sandbox scenario (offline, deterministic, no LLM/network).
# Contract (deploy-skill.sh): runs under `env -i HOME=... PATH=/usr/bin:/bin`,
# must print SCENARIO-PASS on success and exit nonzero on any failure.
set -euo pipefail

skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
export PYTHONPYCACHEPREFIX="$work/pycache"
cd "$work"

export MEETING_NOTES_DIR="$work/notes"
export MEETING_STATE_FILE="$work/state/milestones.yaml"
export MEETING_RULES_FILE="$skill_dir/configs/sensitivity-rules.yaml"
export MEETING_PROMPT_FILE="$skill_dir/prompts/meeting-extraction-v3.md"
export MEETING_LOG_DIR="$work/logs"
export MEETING_PLAN_DIR="$work/plan"
export MEETING_CONFIG="$work/no-config.json"

cli="$skill_dir/scripts/meeting_cli.py"
fx="$skill_dir/fixtures"

echo "[1] compile"
python3 -m py_compile "$skill_dir"/scripts/*.py "$skill_dir"/plugin/__init__.py

echo "[2] clean md ingest (recorded LLM) -> cards>=3, milestones>=2, team post"
python3 "$cli" ingest --file "$fx/meeting-clean.md" \
  --recorded-response "$fx/recorded-clean.json" --offline --notify-channel SANDBOX
card_lines=$(wc -l < "$work/plan/kanban-plan.jsonl")
[ "$card_lines" -ge 3 ] || { echo "FAIL cards=$card_lines"; exit 1; }
milestone_count=$(grep -c '^  - title: ' "$work/state/milestones.yaml")
[ "$milestone_count" -ge 2 ] || { echo "FAIL milestones=$milestone_count"; exit 1; }
head -1 "$work/plan/team-post.txt" | grep -q '```json' || { echo "FAIL team post"; exit 1; }
grep -q '회의록 처리 완료' "$work/plan/notify.txt" || { echo "FAIL notify"; exit 1; }

echo "[3] patent md ingest -> sensitive, sanitized card/state, NO team post"
rm -rf "$work/plan" && mkdir -p "$work/plan"
rm -f "$work/state/milestones.yaml"
python3 "$cli" ingest --file "$fx/meeting-patent.md" \
  --recorded-response "$fx/recorded-patent.json" --offline --notify-channel SANDBOX \
  | grep -q '"sensitive": true' || { echo "FAIL not sensitive"; exit 1; }
for banned in 특허 출원 청구항 claim 변리사 기술이전 선행기술; do
  if grep -qi "$banned" "$work/plan/kanban-plan.jsonl" "$work/state/milestones.yaml" "$work/plan/notify.txt"; then
    echo "FAIL sanitization leak: $banned"; exit 1
  fi
done
[ ! -f "$work/plan/team-post.txt" ] || { echo "FAIL sensitive team post exists"; exit 1; }
grep -q '청구항' "$work/notes/"*.md || { echo "FAIL original detail missing from note"; exit 1; }

echo "[4] glm fail-closed guard (call_litellm refuses sensitive input)"
python3 - "$skill_dir" <<'PY'
import sys
sys.path.insert(0, sys.argv[1] + "/scripts")
import meeting_llm
try:
    meeting_llm.call_litellm("x", sensitive=True, base_url="http://127.0.0.1:1", api_key="n")
except meeting_llm.PatentRoutingError:
    sys.exit(0)
sys.exit(1)
PY

echo "[5] 30MiB reject"
dd if=/dev/zero of="$work/big.md" bs=1 count=1 seek=31457279 status=none
rc=0; python3 "$cli" ingest --file "$work/big.md" --offline >/dev/null || rc=$?
[ "$rc" -eq 3 ] || { echo "FAIL size rc=$rc"; exit 1; }

echo "[6] scanned pdf -> manual conversion request"
python3 "$skill_dir/scripts/make_fixture_pdf.py" "$work/scan.pdf" --scanned
rc=0; out=$(python3 "$cli" ingest --file "$work/scan.pdf" --offline) || rc=$?
[ "$rc" -eq 4 ] || { echo "FAIL scanned rc=$rc"; exit 1; }
echo "$out" | grep -q '수동 변환 요청' || { echo "FAIL scanned notice"; exit 1; }

echo "[7] text pdf ingest works"
python3 "$skill_dir/scripts/make_fixture_pdf.py" "$work/text.pdf" --text
python3 "$cli" ingest --file "$work/text.pdf" \
  --recorded-response "$fx/recorded-clean.json" --offline \
  | grep -q '"provider": "recorded"' || { echo "FAIL pdf ingest"; exit 1; }

echo "SCENARIO-PASS"
