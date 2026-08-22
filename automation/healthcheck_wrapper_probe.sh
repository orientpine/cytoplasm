#!/usr/bin/env bash
# healthcheck 전용 ssh 키의 강제명령 래퍼가 지금의 체크 목록과 맞는지 본다.
#
# 래퍼는 명령 해시 정확 일치만 통과시키므로, 체크를 추가하고 래퍼를 재생성하지 않으면
# 그 체크는 **조용히 exit 126** 을 받는다. 조용한 이유는 구조적이다 — 거부당한 프로브는
# 자기 하나만 UNKNOWN/FAIL 이 되고, 그 실패가 수리 티켓을 내려 해도 티켓 명령 역시
# 같은 이유로 거부되기 때문에 밖으로 나가는 신호가 없다.
#
# 2026-08-20 실측: 보내지는 명령 43개 중 23개가 목록 밖이었고, 더는 쓰이지 않는 해시
# 14개가 남아 있었다. 두 방향 모두 아무 경보도 내지 않았다.
#
# 판정은 래퍼 헤더의 `# wrapper-inputs:` 한 줄로 한다. 생성기가 그 값을 LIVE_CHECKS 와
# 워처 매니페스트에서 계산해 박아두므로, 매 틱 전수 기록을 다시 돌릴 필요가 없다.
wrapper_probe_log() { printf '[healthcheck-wrapper] %s\n' "$*" >&2; }

wrapper_probe_guidance() { # wrapper_probe_guidance <node>
  wrapper_probe_log \
    "regenerate on the node (no root needed — the operator owns the file):"
  wrapper_probe_log \
    "  bash /srv/autophagy-agent-current/automation/healthcheck_probe_wrapper.sh --install $1"
}

probe_healthcheck_wrapper_current() {
  local node="$1" _account="$2" target="$3"
  local source_root="${HEALTHCHECK_RELEASE_SOURCE_ROOT:-/srv/autophagy-agent-current}"
  local generator="$source_root/automation/healthcheck_probe_wrapper.sh"
  local expected installed

  if [[ ! -r "$generator" ]]; then
    wrapper_probe_log "WRAPPER-DRIFT-UNKNOWN: generator missing at $generator"
    return 1
  fi
  if ! expected="$(bash "$generator" --inputs-digest "$node" 2>/dev/null)" \
    || [[ ! "$expected" =~ ^[0-9a-f]{64}$ ]]; then
    wrapper_probe_log "WRAPPER-DRIFT-UNKNOWN: could not compute the expected inputs digest"
    return 1
  fi

  # 래퍼 자신이 이 명령을 거부하면 그것이 곧 답이다 — 재생성되지 않았다는 뜻이다.
  # (거부와 노드 불통을 여기서 가르지 않는다: 어느 쪽이든 사람이 봐야 하고, 안내는 같다.)
  if ! installed="$(capture_on_node "$node" \
    "sed -n 's/^# wrapper-inputs: //p' \"\$HOME/.local/libexec/autophagy-healthcheck-probe\"")"; then
    wrapper_probe_log "WRAPPER-DRIFT: the wrapper rejected this probe or the node is unreachable"
    wrapper_probe_guidance "$node"
    return 1
  fi
  installed="${installed//[[:space:]]/}"

  if [[ -z "$installed" ]]; then
    wrapper_probe_log "WRAPPER-DRIFT: the installed wrapper carries no provenance header"
    wrapper_probe_log "  it predates the generator and was maintained by hand — regenerate it"
    wrapper_probe_guidance "$node"
    return 1
  fi
  if [[ "$installed" != "$expected" ]]; then
    wrapper_probe_log "WRAPPER-DRIFT: the wrapper was built from an older check list"
    wrapper_probe_log "  installed=${installed:0:12} expected=${expected:0:12}"
    wrapper_probe_guidance "$node"
    return 1
  fi

  wrapper_probe_log "WRAPPER-PASS: the allowlist matches the current checks (${expected:0:12})"
  : "${target:-}"
}
