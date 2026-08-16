#!/usr/bin/env bash
set -euo pipefail

# Self-contained on purpose (skill_mount_probe.sh's shape): it resolves the node's
# accounts and paths itself instead of leaning on the sourcing script, so the probe
# stays readable and runnable outside healthcheck.sh. healthcheck.sh already eval'd
# the very same assignments, so the guard keeps this to one python3 call per shell.
if [[ -z "${NODE_AGENT_HOME:-}" || -z "${NODE_AGENT_ACCOUNT:-}" || -z "${NODE_SKILL_STORE:-}" ]]; then
  eval "$(python3 "$(dirname "${BASH_SOURCE[0]}")/node_config_sh.py" --print-env)"
fi

readonly AGENT_HOME="${HEALTHCHECK_SELFSKILL_AGENT_HOME:-$NODE_AGENT_HOME}"
readonly AGENT_SKILLS_ROOT="${AGENT_HOME}/.hermes/skills"
readonly AGENT_SKILLS_CONFIG="${AGENT_HOME}/.hermes/config.yaml"
readonly GOVERNED_SKILLS_ROOT="${HEALTHCHECK_SELFSKILL_LIVE_ROOT:-$NODE_SKILL_STORE/live}"
readonly AGENT_ROOT_EXPECTED="$NODE_AGENT_ACCOUNT:$NODE_AGENT_ACCOUNT:700"
readonly GOVERNED_ROOT_EXPECTED="root:root:755"
readonly AGENT_EXTERNAL_DIRS_KEY="external_dirs:"
readonly AGENT_EXTERNAL_DIR_ENTRY="- $GOVERNED_SKILLS_ROOT"
readonly AGENT_GUARD_LINE="guard_agent_created: true"

selfskill_root_log() {
  printf '[healthcheck] %s\n' "$1"
}

selfskill_root_guidance() {
  printf '%s\n' \
    'SELFSKILL-ROOT-RECOVERY: re-run sudo bash automation/provision-skill-roots.sh' \
    'SELFSKILL-ROOT-ROLLBACK: docs/patch/2026-08-15-agent-selfskill-root-inversion.md#rollback'
}

probe_selfskill_root_topology() {
  local _node="$1" _account="$2" target="$3" root_metadata live_metadata skills_block

  [[ "$target" == "$GOVERNED_SKILLS_ROOT" ]] || {
    selfskill_root_log "SELFSKILL-LIVE-PATH-MISMATCH expected=${GOVERNED_SKILLS_ROOT} actual=${target}"
    return 1
  }
  if [[ ! -e "$AGENT_HOME" && ! -e "$GOVERNED_SKILLS_ROOT" && ! -L "$GOVERNED_SKILLS_ROOT" ]]; then
    return 0
  fi
  if mountpoint -q "$AGENT_SKILLS_ROOT"; then
    selfskill_root_log "SELFSKILL-ROOT-MOUNTPOINT path=${AGENT_SKILLS_ROOT}"
    return 1
  fi
  root_metadata="$(stat -c '%U:%G:%a' -- "$AGENT_SKILLS_ROOT" 2>/dev/null)" || root_metadata='<unavailable>'
  if [[ "$root_metadata" != "$AGENT_ROOT_EXPECTED" ]]; then
    selfskill_root_log "SELFSKILL-ROOT-OWNER-MODE path=${AGENT_SKILLS_ROOT} expected=${AGENT_ROOT_EXPECTED} actual=${root_metadata}"
    return 1
  fi
  if [[ ! -r "$AGENT_SKILLS_CONFIG" ]]; then
    selfskill_root_log "SELFSKILL-CONFIG-UNREADABLE path=${AGENT_SKILLS_CONFIG}"
    return 1
  fi
  skills_block="$(
    awk '
      /^skills:[[:space:]]*$/ { inside = 1; next }
      inside && /^[^[:space:]#]/ { exit }
      inside { print }
    ' "$AGENT_SKILLS_CONFIG" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//'
  )"
  if ! grep -Fxq "$AGENT_EXTERNAL_DIRS_KEY" <<< "$skills_block" \
    || ! grep -Fxq -- "$AGENT_EXTERNAL_DIR_ENTRY" <<< "$skills_block"; then
    selfskill_root_log "SELFSKILL-CONFIG-EXTERNAL-DIRS-MISSING path=${GOVERNED_SKILLS_ROOT}"
    return 1
  fi
  if ! grep -Fxq "$AGENT_GUARD_LINE" <<< "$skills_block"; then
    selfskill_root_log "SELFSKILL-CONFIG-GUARD-MISSING path=${AGENT_SKILLS_CONFIG}"
    return 1
  fi
  live_metadata="$(stat -c '%U:%G:%a' -- "$GOVERNED_SKILLS_ROOT" 2>/dev/null)" || live_metadata='<unavailable>'
  if [[ "$live_metadata" != "$GOVERNED_ROOT_EXPECTED" ]]; then
    selfskill_root_log "SELFSKILL-LIVE-OWNER-MODE expected=${GOVERNED_ROOT_EXPECTED} actual=${live_metadata}"
    return 1
  fi
}
