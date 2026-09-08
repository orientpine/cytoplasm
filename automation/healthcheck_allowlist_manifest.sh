#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly MANIFEST_FILE="${HEALTHCHECK_ALLOWLIST_MANIFEST_FILE:-$SCRIPT_DIR/healthcheck_allowlist_manifest.example.txt}"
readonly MODE="${1:-}"

# shellcheck source=automation/healthcheck_command_builder.sh
source "$SCRIPT_DIR/healthcheck_command_builder.sh"
# shellcheck source=automation/healthcheck.sh
if [[ "$MODE" != "--probe-hashes" ]]; then
  export HEALTHCHECK_NODE_CONFIG_PATH="$SCRIPT_DIR/../configs/node.example.toml"
fi
source "$SCRIPT_DIR/healthcheck.sh"
# shellcheck source=automation/healthcheck_probe_wrapper.sh
source "$SCRIPT_DIR/healthcheck_probe_wrapper.sh"
readonly SYNTHETIC_CHECK="synthetic nonexistent ops unit|user_unit_active|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|autophagy-healthcheck-synthetic-does-not-exist.service"

print_manifest() {
  local entry definition check_name
  # The sweep may be narrowed, but --suggest always examines the full catalog. Its
  # wrapper must therefore allow every discovery command; otherwise an excluded command
  # returns 126 and looks exactly like an absent service. Catalog order is LIVE_CHECKS
  # order, so the default/full manifest remains byte-identical.
  for entry in "${HEALTHCHECK_CHECK_CATALOG[@]}"; do
    definition="${entry#*|}"
    IFS='|' read -r check_name _ <<< "$definition"
    healthcheck_repair_command "$check_name"
    printf '\n'
  done
  IFS='|' read -r check_name _ <<< "$SYNTHETIC_CHECK"
  healthcheck_repair_command "$check_name"
  printf '\n'
}

case "$MODE" in
  --print) print_manifest ;;
  --probe-hashes) wrapper_command_manifest "${2:-$PRIMARY_NODE}" ;;
  --check)
    if ! cmp -s <(print_manifest) "$MANIFEST_FILE"; then
      printf '%s\n' "manifest mismatch; regenerate with: bash automation/healthcheck_allowlist_manifest.sh --print > automation/healthcheck_allowlist_manifest.example.txt" >&2
      exit 1
    fi
    ;;
  *)
    printf 'Usage: healthcheck_allowlist_manifest.sh --print|--check|--probe-hashes [node]\n' >&2
    exit 2
    ;;
esac
