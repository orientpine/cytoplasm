#!/usr/bin/env bash

readonly UPDATE_TRUST_PROBE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

probe_update_trust() {
  local _node="$1" _account="$2" checkout="$3"
  local python="${UPDATE_TRUST_PYTHON:-python3}"
  local script="${UPDATE_TRUST_SCRIPT:-$UPDATE_TRUST_PROBE_DIR/update_trust.py}"
  local target="${HEALTHCHECK_OPS_CHECKOUT:-$checkout}" output command_status=0
  [[ "$target" == /* ]] || return 1
  output="$("$python" "$script" resolve --mirror "$target" 2>&1)" || command_status=$?
  (( command_status == 0 )) && return 0
  if [[ "$output" == UPDATE-TRUST-BLOCK* ]]; then
    printf '[update-trust] %s\n' "$output" >&2
  else
    printf '[update-trust] UPDATE-TRUST-BLOCK %s\n' "$output" >&2
  fi
  return 1
}
