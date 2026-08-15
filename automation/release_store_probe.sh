#!/usr/bin/env bash

release_store_log() {
  printf '[healthcheck] %s\n' "$1"
}

release_store_guidance() {
  printf '%s\n' \
    "릴리스 스토어가 retention 또는 용량 한계를 넘었다." \
    "조치: current 대상을 보존하고 다음 정상 landing으로 pruning을 재실행한 뒤 개수·용량을 확인한다." \
    "임의 rm은 직전 세대 롤백을 훼손할 수 있으므로 사용하지 않는다."
}

probe_release_store_usage() {
  local node="$1" account="$2" target="$3"
  local store="${HEALTHCHECK_RELEASE_STORE_ROOT:-$target}"
  local max_generations="${RELEASE_STORE_MAX_GENERATIONS:-6}"
  local max_bytes="${RELEASE_STORE_MAX_BYTES:-1073741824}"
  local path usage bytes generations=0
  [[ "$store" == /* ]] || return 1
  [[ "$max_generations" =~ ^[0-9]+$ && "$max_bytes" =~ ^[0-9]+$ ]] || return 1
  if [[ ! -e "$store" && ! -L "$store" ]]; then
    release_store_log "RELEASE-STORE-USAGE generations=0 bytes=0 max_generations=$max_generations max_bytes=$max_bytes"
    return 0
  fi
  [[ -d "$store" && ! -L "$store" ]] || return 1
  for path in "$store"/*; do
    [[ -d "$path" && ! -L "$path" && "${path##*/}" =~ ^[0-9a-f]{40,64}$ ]] \
      && generations=$((generations + 1))
  done
  usage="$(du -sb -- "$store")" || return 1
  bytes="${usage%%[[:space:]]*}"
  [[ "$bytes" =~ ^[0-9]+$ ]] || return 1
  release_store_log "RELEASE-STORE-USAGE generations=$generations bytes=$bytes max_generations=$max_generations max_bytes=$max_bytes"
  (( generations <= max_generations && bytes <= max_bytes ))
}
