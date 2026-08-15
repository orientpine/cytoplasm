#!/usr/bin/env bash
set -euo pipefail

fail() { printf 'SCENARIO-FAIL %s\n' "$1" >&2; exit 1; }
secret="${AUTOPHAGY_DEMO_SECRET:-}"
[[ "$secret" == DUMMY-* ]] || fail "dummy secret required"
[[ "$secret" != *sk-* && "$secret" != *ghp_* && "$secret" != *"Bot "* ]] || fail "token-shaped dummy"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"
mkdir -p notes outputs
printf '# Alpha\n\nclean evidence one\n' > notes/alpha.md
printf '# Beta\n\nclean evidence two\n' > notes/beta.md
printf '# Gamma\n\nclean evidence three\n' > notes/gamma.md
printf 'Concise draft.' > response.md
cli() {
  python3 -I -c '
import runpy
import sys
from pathlib import Path

script_dir = sys.argv[1]
sys.path.insert(0, str(Path(script_dir).parents[1]))
sys.argv = [f"{script_dir}/report_cli.py", *sys.argv[2:]]
runpy.run_path(sys.argv[0], run_name="__main__")
' "$script_dir" "$@"
}

report_out="$(cli report --notes-root "$work/notes" --outputs-root "$work/outputs" --response-file "$work/response.md")"
report_path="$(printf '%s\n' "$report_out" | sed -n 's/^REPORT-CREATED path=\([^ ]*\).*/\1/p')"
[[ -f "$report_path" ]] || fail "report absent"
grep -q '^## 자료 범위$' "$report_path" || fail "report sections"
slides_out="$(cli slides --report "$report_path" --outputs-root "$work/outputs")"
slides_path="$(printf '%s\n' "$slides_out" | sed -n 's/^SLIDES-CREATED path=\([^ ]*\).*/\1/p')"
[[ -f "$slides_path" ]] || fail "slides absent"
[[ "$(grep -c '<section' "$slides_path")" -eq 4 ]] || fail "slide count"
script_out="$(cli script --report "$report_path" --slides "$slides_path" --outputs-root "$work/outputs")"
script_path="$(printf '%s\n' "$script_out" | sed -n 's/^SCRIPT-CREATED path=\([^ ]*\).*/\1/p')"
[[ -f "$script_path" ]] || fail "script absent"
grep -q '^# 발표 대본$' "$script_path" || fail "script structure"
empty_out="$(cli report --notes-root "$work/empty" --outputs-root "$work/outputs")"
grep -q '자료 부족' <<<"$empty_out" || fail "empty notes"
printf '# Sensitive\n\npatent planning\n' > notes/sensitive.md
sensitive_out="$(cli report --notes-root "$work/notes" --query patent --outputs-root "$work/outputs" --response-file "$work/response.md")"
grep -q 'provider=openai-codex' <<<"$sensitive_out" || fail "sensitive route"

printf 'SCENARIO-PASS report=true slides=4 script=true sensitive=codex empty=true account=%s\n' "$(whoami)"
