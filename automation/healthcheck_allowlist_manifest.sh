#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly MANIFEST_FILE="${HEALTHCHECK_ALLOWLIST_MANIFEST_FILE:-$SCRIPT_DIR/healthcheck_allowlist_manifest.example.txt}"

# shellcheck source=automation/healthcheck_command_builder.sh
source "$SCRIPT_DIR/healthcheck_command_builder.sh"
# shellcheck source=automation/healthcheck.sh
source "$SCRIPT_DIR/healthcheck.sh"

print_manifest() {
  local definition check_name
  local synthetic="synthetic nonexistent ops unit|user_unit_active|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|autophagy-healthcheck-synthetic-does-not-exist.service"
  for definition in "${LIVE_CHECKS[@]}" "$synthetic"; do
    IFS='|' read -r check_name _ <<< "$definition"
    healthcheck_repair_command "$check_name"
    printf '\n'
  done
}

case "${1:-}" in
  --print) print_manifest ;;
  --check)
    if ! cmp -s <(print_manifest) "$MANIFEST_FILE"; then
      printf '%s\n' "manifest mismatch; regenerate with: bash automation/healthcheck_allowlist_manifest.sh --print > automation/healthcheck_allowlist_manifest.example.txt" >&2
      exit 1
    fi
    ;;
  *) printf 'Usage: healthcheck_allowlist_manifest.sh --print|--check\n' >&2; exit 2 ;;
esac
