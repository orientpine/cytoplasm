#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EVIDENCE_DIR="$ROOT/docs/qa/F2"
LOC_EXCEPTIONS="$ROOT/automation/final/f2_loc_exceptions.txt"
RUN_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "$EVIDENCE_DIR"
umask 077

summary="$EVIDENCE_DIR/summary.txt"
: >"$summary"
printf 'F2 final quality audit\nrun_at=%s\nrepo=%s\n\n' "$RUN_AT" "$ROOT" >>"$summary"

failed=0

run_check() {
  local name="$1"
  shift
  local output="$EVIDENCE_DIR/$name.txt"

  printf '\n=== %s ===\n' "$name" >>"$summary"
  {
    printf '$'
    printf ' %q' "$@"
    printf '\n'
  } >>"$output"

  "$@" >>"$output" 2>&1
  local status=$?
  printf 'exit=%d\n' "$status" >>"$output"
  printf '%s: exit=%d\n' "$name" "$status" >>"$summary"
  if [[ $status -ne 0 ]]; then
    failed=1
  fi
}

cd "$ROOT"

# Keep this aligned with CI: vendored third-party mail code is byte-identical
# upstream source and is not part of this repository's Ruff convention.
run_check ruff ruff check . --exclude skills/mail/vendor
run_check gitleaks gitleaks detect --log-opts=--all --redact

loc_report="$EVIDENCE_DIR/module-loc.txt"
: >"$loc_report"
loc_failed=0
if [[ ! -f "$LOC_EXCEPTIONS" ]]; then
  printf 'LOC exception registry missing: %s\n' "$LOC_EXCEPTIONS" >>"$loc_report"
  loc_failed=1
fi
while IFS= read -r path; do
  [[ "$path" == automation/* || "$path" == skills/* ]] || continue
  case "$path" in
    *.py|*.sh) ;;
    *) continue ;;
  esac
  pure_loc="$(awk '/^[[:space:]]*($|#)/ { next } { count += 1 } END { print count + 0 }' "$path")"
  printf '%4d %s\n' "$pure_loc" "$path" >>"$loc_report"
  if (( pure_loc > 250 )); then
    exception="$(grep -F "$path | " "$LOC_EXCEPTIONS" 2>/dev/null || true)"
    if [[ -n "$exception" ]]; then
      printf 'EXCEPTION %s\n' "$exception" >>"$loc_report"
    else
      printf 'VIOLATION %s\n' "$path" >>"$loc_report"
      loc_failed=1
    fi
  fi
done < <(GIT_MASTER=1 git ls-files)

sort -nr -k1,1 -k2,2 -o "$loc_report" "$loc_report"
if [[ ! -s "$loc_report" ]]; then
  printf 'No tracked production Python or shell modules found.\n' >>"$loc_report"
  loc_failed=1
fi
if (( loc_failed )); then
  printf 'LOC RESULT: FAIL (one or more modules exceed 250 pure LOC without an explicit exception)\n' >>"$loc_report"
  failed=1
else
  printf 'LOC RESULT: PASS (every over-limit module is explicitly reported and exception-documented)\n' >>"$loc_report"
fi
printf 'module-loc: exit=%d\n' "$loc_failed" >>"$summary"

secret_report="$EVIDENCE_DIR/secret-patterns.txt"
: >"$secret_report"
secret_pattern='(ghp_[[:alnum:]]{36,}|github_pat_[[:alnum:]_]{82,}|sk-[[:alnum:]_-]{20,}|AIza[[:alnum:]_-]{35}|AKIA[[:alnum:]]{16}|Bearer[[:space:]]+[[:alnum:]_.-]{20,})'
GIT_MASTER=1 git grep -nEI "$secret_pattern" -- \
  ':!docs/qa/**' ':!*.md' ':!tests/**' >"$secret_report" 2>&1
secret_status=$?
case "$secret_status" in
  0)
    printf 'exit=1\nSECRET RESULT: FAIL (secret-shaped tracked content found)\n' >>"$secret_report"
    failed=1
    ;;
  1)
    printf 'exit=0\nSECRET RESULT: PASS (no secret-shaped tracked content found)\n' >>"$secret_report"
    ;;
  *)
    printf 'exit=%d\nSECRET RESULT: FAIL (grep execution failed)\n' "$secret_status" >>"$secret_report"
    failed=1
    ;;
esac
printf 'secret-patterns: exit=%d\n' "$([[ $secret_status -eq 1 ]] && printf 0 || printf 1)" >>"$summary"

if (( failed )); then
  printf '\nF2 RESULT: FAIL\n' >>"$summary"
  exit 1
fi

printf '\nF2 RESULT: PASS\n' >>"$summary"
