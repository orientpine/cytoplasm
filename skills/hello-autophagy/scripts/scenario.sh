#!/usr/bin/env bash
# Sandbox scenario for the hello-autophagy demo skill (W1-8 pipeline stage 1).
# MUST run with DUMMY secrets only. Refuses anything that looks real.
set -euo pipefail

fail() { printf 'SCENARIO-FAIL %s\n' "$1" >&2; exit 1; }

secret="${AUTOPHAGY_DEMO_SECRET:-}"
[[ -n "$secret" ]] || fail "AUTOPHAGY_DEMO_SECRET is not set"
[[ "$secret" == DUMMY-* ]] || fail "secret does not carry the DUMMY- prefix (real secrets are forbidden in sandbox)"
if [[ "$secret" == *sk-* || "$secret" == *ghp_* || "$secret" == *"Bot "* ]]; then
  fail "secret matches a real-token shape"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
output="$("$script_dir/hello.sh")"
[[ "$output" == HELLO-AUTOPHAGY* ]] || fail "hello.sh output missing HELLO-AUTOPHAGY marker"

printf '%s\n' "$output"
printf 'SCENARIO-PASS secret_len=%s account=%s\n' "${#secret}" "$(whoami)"
