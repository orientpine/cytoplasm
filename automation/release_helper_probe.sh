#!/usr/bin/env bash
release_helper_probe_log() { printf '[release-helper] %s\n' "$*" >&2; }

probe_release_helper_drift() {
  local _node="$1" _account="$2" _target="$3"
  local helper="${HEALTHCHECK_RELEASE_HELPER:-/usr/local/libexec/autophagy-install-release}"
  local provenance="${HEALTHCHECK_RELEASE_PROVENANCE:-/usr/local/libexec/release_provenance.py}"
  local source_root="${HEALTHCHECK_RELEASE_SOURCE_ROOT:-/srv/autophagy-agent-current}"
  local source_helper="$source_root/automation/release_store.py"
  local source_provenance="$source_root/automation/release_provenance.py"
  local installed_hash source_hash

  for path in "$helper" "$provenance" "$source_helper" "$source_provenance"; do
    if [[ ! -r "$path" ]]; then
      release_helper_probe_log "HELPER-DRIFT-UNKNOWN: unreadable path=$path"
      return 1
    fi
  done
  installed_hash="$(sha256sum -- "$helper" | cut -d' ' -f1)"
  source_hash="$(sha256sum -- "$source_helper" | cut -d' ' -f1)"
  if [[ "$installed_hash" != "$source_hash" ]]; then
    release_helper_probe_log "HELPER-DRIFT: autophagy-install-release differs from release source"
    return 1
  fi
  installed_hash="$(sha256sum -- "$provenance" | cut -d' ' -f1)"
  source_hash="$(sha256sum -- "$source_provenance" | cut -d' ' -f1)"
  if [[ "$installed_hash" != "$source_hash" ]]; then
    release_helper_probe_log "HELPER-DRIFT: release_provenance.py differs from release source"
    return 1
  fi
  release_helper_probe_log "HELPER-DRIFT-PASS: privileged helpers match release sources"
}
