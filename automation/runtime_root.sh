#!/usr/bin/env bash
# automation/runtime_root.sh — bash twin of runtime_root.py (DG-4).
#
# Resolve the runtime root for shell consumers, identically to the Python
# resolver (a test pins them byte-for-byte):
#   1. AUTOPHAGY_RUNTIME_ROOT (explicit override), else
#   2. the release symlink /srv/autophagy-agent-current, if it exists, else
#   3. the resident /srv/autophagy-agents mirror (backwards-compatible fallback).
#
# Sourced, not executed:  source automation/runtime_root.sh; autophagy_runtime_root

autophagy_runtime_root() {
  local current="${RUNTIME_RELEASE_CURRENT:-/srv/autophagy-agent-current}"
  local mirror="${RUNTIME_MIRROR_CHECKOUT:-/srv/autophagy-agents}"
  if [[ -n "${AUTOPHAGY_RUNTIME_ROOT:-}" ]]; then
    printf '%s\n' "$AUTOPHAGY_RUNTIME_ROOT"
  elif [[ -e "$current" ]]; then
    printf '%s\n' "$current"
  else
    printf '%s\n' "$mirror"
  fi
}
