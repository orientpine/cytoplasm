#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLAN="$ROOT/.omo/plans/autophagy-agents.md"
QA_ROOT="$ROOT/docs/qa"
EVIDENCE_DIR="$QA_ROOT/F1"
SUMMARY="$EVIDENCE_DIR/summary.txt"
SKIPS="$EVIDENCE_DIR/conditional-skips.txt"

mkdir -p "$EVIDENCE_DIR"
umask 077
: >"$SUMMARY"
: >"$SKIPS"

if [[ ! -f "$PLAN" ]]; then
  printf 'F1 RESULT: FAIL\nplan missing: %s\n' "$PLAN" | tee -a "$SUMMARY"
  exit 1
fi

mapfile -t todos < <(
  awk '
    /^#### \[[x~]\] W[0-9]+-[0-9]+[A-Za-z0-9-]*/ {
      status = substr($0, 7, 1)
      if (match($0, /W[0-9]+-[0-9]+[A-Za-z0-9-]*/)) {
        print status " " substr($0, RSTART, RLENGTH)
      }
    }
  ' "$PLAN" | sort -u
)

if (( ${#todos[@]} == 0 )); then
  printf 'F1 RESULT: FAIL\nno executed todo headings parsed\n' | tee -a "$SUMMARY"
  exit 1
fi

should_skip_conditional() {
  local todo_id="$1"
  case "$todo_id" in
    W1-5F)
      if grep -Fq '# W1-5 bot-interop gate — PASS' "$QA_ROOT/W1-5/02-gate-verdict.md" 2>/dev/null; then
        printf '%s\n' 'W1-5F SKIP: W1-5 gate PASS is documented in docs/qa/W1-5/02-gate-verdict.md.'
        return 0
      fi
      ;;
    W4-1N)
      if grep -Eq '"mode"[[:space:]]*:[[:space:]]*"full-go"' "$ROOT/configs/mail-mode.default.json" 2>/dev/null; then
        printf '%s\n' 'W4-1N SKIP: configs/mail-mode.default.json documents the full-go branch.'
        return 0
      fi
      ;;
  esac
  return 1
}

missing=0
present=0
skipped=0
printf 'F1 plan-adherence audit\nplan=%s\n\n' "$PLAN" >>"$SUMMARY"

for todo in "${todos[@]}"; do
  status="${todo%% *}"
  todo_id="${todo#* }"
  if skip_note="$(should_skip_conditional "$todo_id")"; then
    printf '%s\n' "$skip_note" | tee -a "$SKIPS" "$SUMMARY"
    ((skipped += 1))
    continue
  fi

  qa_dir="$QA_ROOT/$todo_id"
  if [[ -d "$qa_dir" ]] && [[ -n "$(find "$qa_dir" -mindepth 1 -print -quit)" ]]; then
    printf 'PRESENT %s status=[%s] evidence=%s\n' "$todo_id" "$status" "docs/qa/$todo_id/" >>"$SUMMARY"
    ((present += 1))
  else
    printf 'MISSING %s status=[%s] expected=docs/qa/%s/\n' "$todo_id" "$status" "$todo_id" | tee -a "$SUMMARY"
    ((missing += 1))
  fi
done

printf '\nexecuted=%d present=%d skipped=%d missing=%d\n' "${#todos[@]}" "$present" "$skipped" "$missing" >>"$SUMMARY"
if (( missing == 0 )); then
  printf 'F1 RESULT: PASS\n' >>"$SUMMARY"
  exit 0
fi

printf 'F1 RESULT: FAIL\n' >>"$SUMMARY"
exit 1
