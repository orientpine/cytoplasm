#!/usr/bin/env bash
set -euo pipefail

fail() { printf 'SCENARIO-FAIL %s\n' "$1" >&2; exit 1; }

secret="${AUTOPHAGY_DEMO_SECRET:-}"
[[ "$secret" == DUMMY-* ]] || fail "dummy secret required"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

cli() {
  TOPICS_STATE_FILE="$work/research-topics.yaml" TOPICS_RULES_PATH="$work/rules.yaml" \
    python3 -I "$script_dir/topics_cli.py" "$@"
}

cli add "autophagy flux" | grep -qx 'TOPIC-ADDED autophagy flux' || fail "safe add"
cli list | grep -qx -- '- autophagy flux' || fail "list"
cli suggest "restricted-marker analysis" | grep -q '^TOPIC-SUGGEST-REFUSED ' || fail "suggest gate"
cli add "restricted-marker analysis" | grep -q '^TOPIC-REFUSED ' || fail "add gate"
grep -q 'restricted-marker' "$work/research-topics.yaml" && fail "sensitive topic persisted"
cli remove "autophagy flux" | grep -qx 'TOPIC-REMOVED' || fail "remove"
cli list | grep -qx 'TOPICS-EMPTY 등록된 주제가 없습니다.' || fail "empty list"

printf 'SCENARIO-PASS legs=add+list+remove+sensitivity account=%s\n' "$(whoami)"
