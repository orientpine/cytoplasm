#!/usr/bin/env bash
# Hermes no-agent cron 래퍼가 릴리스와 어긋났는지 본다.
#
# 이 래퍼들은 릴리스 트리가 아니라 **계정 홈**(`~/.hermes/scripts/`)에 산다. 2분 리컨실러는
# 릴리스만 수렴시키므로, 워처를 고쳐 머지해도 그 스킬/패키지의 `deploy.sh` 를 사람이 돌리기
# 전까지 노드는 옛 코드를 계속 돌린다 — 그리고 그것을 말해 주는 것이 아무것도 없었다.
# `skill_mounts_current` 는 마운트 스킬을, `release_helper_drift` 는 릴리스 밖 root 자산을
# 보지만 래퍼는 어느 쪽에도 속하지 않았다. 실제 대가: mailon 런타임 19일 방치, 그리고
# 2026-08-20 실측에서 `memory_curator_watch`·`memory_relocate_watch` 두 개가 조용히 낡아 있었다.
#
# **원격 프로브다.** 래퍼는 agent·peer 홈(0700)에 있고 cron 계정(ops)은 그 계정으로 sudo 할 수
# 없다 — `capture_on_node` 가 ssh 하는 `HEALTHCHECK_SSH_USER` 만 읽을 수 있으므로 LOCAL_PROBES
# 에 넣으면 영원히 UNKNOWN 이다.
watcher_drift_log() { printf '[watcher-drift] %s\n' "$*" >&2; }

#: 재배포 명령은 소스 경로 앞 두 조각에서 유도된다 — `skills/mail/scripts/x.py` →
#: `skills/mail/deploy.sh`. 매니페스트에 네 번째 경로 필드를 두면 드리프트할 사본이 하나 늘 뿐이다.
watcher_drift_deploy_script() { # watcher_drift_deploy_script <source>
  local source="$1" rest
  rest="${source#*/}"
  printf '%s/%s/deploy.sh' "${source%%/*}" "${rest%%/*}"
}

probe_watcher_wrappers_current() {
  local node="$1" _account="$2" manifest="$3"
  local source_root="${HEALTHCHECK_RELEASE_SOURCE_ROOT:-/srv/autophagy-agent-current}"
  local failed=0 compared=0

  [[ -r "$manifest" ]] || {
    watcher_drift_log "WATCHER-DRIFT-UNKNOWN: unreadable manifest=$manifest"
    return 1
  }

  # 매니페스트를 **먼저 통째로** 읽는다. `capture_on_node` 안의 ssh 는 stdin 을 소비하므로
  # while-read 로 파일을 흘리면 두 번째 행부터 사라진다(고전적인 함정).
  local -a rows=()
  mapfile -t rows < "$manifest"

  local row account source destination policy source_sha deployed_sha script
  for row in "${rows[@]}"; do
    [[ -n "${row// /}" ]] || continue
    case "$row" in \#*) continue ;; esac
    IFS='|' read -r account source destination policy <<< "$row"

    # fail-closed: 안전하게 인용할 수 없는 행은 검사할 수 없는 행이다.
    if ! [[ "$account" =~ ^[a-z][a-z0-9_-]*$ ]] \
      || ! [[ "$source" =~ ^[A-Za-z0-9_./-]+$ ]] \
      || ! [[ "$destination" =~ ^[A-Za-z0-9_./-]+$ ]]; then
      watcher_drift_log "WATCHER-DRIFT-UNKNOWN: unsafe manifest row: ${row:0:60}"
      failed=1
      continue
    fi
    if [[ ! -r "$source_root/$source" ]]; then
      watcher_drift_log "WATCHER-DRIFT-UNKNOWN: release source missing: $source"
      failed=1
      continue
    fi

    source_sha="$(sha256sum -- "$source_root/$source" | cut -d' ' -f1)"
    # rc≠ 0 은 “없다”가 아니라 “**보지 못했다**”다 — 둘을 섮으면 노드가 닿지 않거나
    # 명령이 healthcheck 강제명령 allowlist 에 없을 때 전 행을 NOT-DEPLOYED 로 단언해
    # 소유자에게 불필요한 재배포를 지시하게 된다(2026-08-20 프로덕션에서 12행 오탐 실측).
    if ! deployed_sha="$(capture_on_node "$node" \
      "sudo -n -u ${account} -H bash -c 'sha256sum \"\$HOME/${destination}\" 2>/dev/null | cut -d\" \" -f1'")"; then
      watcher_drift_log \
        "WATCHER-DRIFT-UNKNOWN: cannot read ${account}:${destination} — node unreachable or command not allowlisted"
      failed=1
      continue
    fi
    deployed_sha="${deployed_sha//[[:space:]]/}"
    script="$(watcher_drift_deploy_script "$source")"

    if [[ -z "$deployed_sha" ]]; then
      case "$policy" in
        optional:*) continue ;;
        *)
          watcher_drift_log "WATCHER-DRIFT NOT-DEPLOYED: ${account}:${destination} — run $script"
          failed=1
          ;;
      esac
      continue
    fi

    compared=$((compared + 1))
    if [[ "$deployed_sha" != "$source_sha" ]]; then
      watcher_drift_log "WATCHER-DRIFT: ${account}:${destination} differs from the release — run $script"
      failed=1
    fi
  done

  (( failed == 0 )) || return 1
  watcher_drift_log "WATCHER-DRIFT-PASS: ${compared} wrapper(s) match the release"
}
