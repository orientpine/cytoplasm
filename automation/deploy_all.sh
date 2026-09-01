#!/usr/bin/env bash
# automation/deploy_all.sh — origin/main 전량 수렴 오케스트레이터 (RC-3/4).
#
# 지금까지 "배포했다"의 단위는 표면별·스킬별·패키지별이라, 스킬 6개 중 2개만 배포된
# 상태와 전부 배포된 상태가 명령 수준에서 구분되지 않았다(C3). 이 명령은 그 질문을
# 하나로 접는다 — 판정은 노드 릴리스 트리의 `deploy_all_probe.py` 가 내고(관측과 판정이
# 같은 세대의 코드), 실행은 기존 배포기(`deploy-skill.sh`·`<pkg>/deploy.sh`)를 부를
# 뿐이며(배포 로직 사본 0), 마지막 **전량 재판정**을 통과할 때만 영수증을 쓴다 —
# 부분 성공은 성공으로 보고되지 않는다.
#
# Usage: deploy_all.sh [--plan|--verify|--apply [--wait-converge]]
#   --plan   (기본) 판정 출력만. rc 0=전량 일치 · 1=할 일 있음 · 4=판정 불가
#   --verify 판정 + 전량 일치면 영수증 기록(수렴 검증만, 배포 없음)
#   --apply  선택적 --wait-converge 시 노드 릴리스 트리 수렴을 먼저 기다린 뒤
#            계획된 배포기 실행(스킬은 기존 승인 게이트 그대로) → 플러그인 갱신 시
#            게이트웨이 재시동(「게이트웨이 재시동 규칙」— 항상 agent+peer 전 세트)
#            → 전량 재판정 → 영수증
#
# ⑤ root 자산·⑥ RAG·런타임 패키지는 상시 healthcheck 프로브가 소유한다(영수증의
# delegated 필드) — 여기서 실행하지 않고, 어긋남도 그 프로브가 알린다.
#
# Exit: 0 ok(영수증 기록) · 1 drift/재판정 실패 · 2 usage · 3 host 미설정 · 4 판정 불가/전제 미충족
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:---plan}"
wait_converge=0
if [[ "$mode" == "--help" || "$mode" == "-h" ]]; then
  if (( $# > 1 )); then
    echo "usage: deploy_all.sh [--plan|--verify|--apply [--wait-converge]]" >&2
    exit 2
  fi
  echo "usage: deploy_all.sh [--plan|--verify|--apply [--wait-converge]]"
  exit 0
fi
if (( $# > 1 )); then
  if [[ "$mode" == "--apply" && $# == 2 && "$2" == "--wait-converge" ]]; then
    wait_converge=1
  else
    echo "usage: deploy_all.sh [--plan|--verify|--apply [--wait-converge]]" >&2
    exit 2
  fi
fi
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}"
if [[ -z "$host" ]]; then
  echo "DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset. Export it (or configure ~/.hermes/node.toml)." >&2
  exit 3
fi

readonly RUNTIME_ROOT="${DEPLOY_ALL_RUNTIME_ROOT:-/srv/autophagy-agent-current}"
readonly RECEIPT_DIR="${DEPLOY_ALL_RECEIPT_DIR:-/srv/autophagy-private/deploy-all}"

log() { printf '[deploy-all] %s\n' "$*" >&2; }

probe() { # probe <format> — 노드 릴리스 트리의 판정을 그대로 중계한다
  ssh "$host" "python3 $RUNTIME_ROOT/automation/deploy_all_probe.py --format $1" < /dev/null
}

node_release_sha() {
  local target
  target="$(ssh "$host" "readlink $RUNTIME_ROOT" < /dev/null)" || return 1
  basename "${target//[[:space:]]/}"
}

restart_gateways() { # 「게이트웨이 재시동 규칙」(2026-07-22) — 한쪽만 재시동 금지
  local acct
  for acct in agent peer; do
    log "restarting $acct gateway"
    ssh "$host" "sudo -n -u $acct -H bash -lc 'export XDG_RUNTIME_DIR=/run/user/\$(id -u); systemctl --user restart hermes-gateway.service && systemctl --user is-active hermes-gateway.service'" < /dev/null \
      || { log "GATEWAY-RESTART-FAIL: $acct"; return 1; }
  done
}

write_receipt() {
  # 영수증 내용은 노드가 **지금 다시 판정**해 만든다 — 아까의 판정을 재사용하면
  # 그 사이의 변화가 서명된다. read-back 대조는 deploy_push 와 같은 규율이다.
  local receipt want got
  if ! receipt="$(probe receipt)"; then
    log "RECEIPT-BLOCK: the node no longer judges itself clean"
    return 1
  fi
  want="$(printf '%s' "$receipt" | sha256sum | cut -d' ' -f1)"
  printf '%s' "$receipt" | ssh "$host" "sudo -n -u ops -H bash -c 'umask 027; mkdir -p $RECEIPT_DIR && cat > $RECEIPT_DIR/receipt.json'" \
    || { log "RECEIPT-BLOCK: could not write the receipt"; return 1; }
  got="$(ssh "$host" "sudo -n -u ops -H bash -c 'sha256sum $RECEIPT_DIR/receipt.json | cut -d\" \" -f1'" < /dev/null)"
  got="${got//[[:space:]]/}"
  if [[ "$got" != "$want" ]]; then
    log "RECEIPT-BLOCK: read-back mismatch (want=${want:0:16} got=${got:0:16})"
    return 1
  fi
  log "receipt written: $RECEIPT_DIR/receipt.json"
}

case "$mode" in
  --plan)
    probe report
    exit $?
    ;;
  --verify)
    if probe report; then
      write_receipt
      exit $?
    fi
    rc=$?
    (( rc == 1 )) && log "not fully deployed — no receipt (run --apply, or deploy the listed items)"
    exit "$rc"
    ;;
  --apply)
    # 배포기는 이 체크아웃의 파일을 밀므로, 체크아웃과 노드 릴리스가 같은 세대가
    # 아니면 "릴리스로 수렴"이 아니라 "체크아웃으로 오염"이 된다 — provenance 가드와
    # 같은 결의 fail-closed 다.
    local_head="$(git -C "$repo_root" rev-parse HEAD)" || exit 4
    wait_timed_out=0
    if (( wait_converge )); then
      converge_deadline=$(( SECONDS + ${DEPLOY_ALL_CONVERGE_SECONDS:-600} ))
      while :; do
        node_sha="$(node_release_sha)" || node_sha="unreadable"
        [[ "$local_head" == "$node_sha" ]] && break
        log "waiting for the node release (have $node_sha want $local_head)"
        if (( SECONDS >= converge_deadline )); then
          wait_timed_out=1
          break
        fi
        sleep "${DEPLOY_ALL_CONVERGE_POLL_SECONDS:-10}"
      done
    else
      node_sha="$(node_release_sha)" || { log "RELEASE-UNKNOWN: cannot read the node release pointer"; exit 4; }
    fi
    if [[ "$local_head" != "$node_sha" ]]; then
      (( wait_timed_out )) && log "node release convergence wait timed out"
      log "RELEASE-MISMATCH: local HEAD ${local_head:0:12} != node release ${node_sha:0:12}"
      log "  릴리스 수렴(2분 리컨실러)을 기다리거나 체크아웃을 그 sha 로 맞춘 뒤 다시 실행"
      exit 4
    fi
    actions="$(probe actions)"
    rc=$?
    if (( rc == 4 )); then
      log "UNVERIFIABLE: the node cannot judge itself"
      exit 4
    fi
    if (( rc == 0 )); then
      log "already fully deployed"
      write_receipt
      exit $?
    fi
    restart_needed=0
    failures=()
    # actions 는 fd 9 로 읽는다 — 배포기 내부의 ssh 가 fd 0 을 삼키므로, stdin 에
    # 실으면 첫 action 뒤의 줄이 전부 사라진다(2026-08-31 실측: 매 실행 1건만 배포).
    while IFS='|' read -r -u 9 tag kind arg; do
      [[ "$tag" == "ACT" ]] || continue
      case "$kind" in
        deploy-skill)
          log "deploying skill: $arg"
          "$repo_root/automation/deploy-skill.sh" "$arg" --release-approval \
            || failures+=("skill:$arg")
          ;;
        run-deployer)
          log "running deployer: $arg"
          "$repo_root/$arg" || failures+=("$arg")
          ;;
        restart-gateway)
          restart_needed=1
          ;;
        manual)
          log "MANUAL: $arg — 자동 수렴 대상이 아님"
          failures+=("manual:$arg")
          ;;
      esac
    done 9<<< "$actions"
    if (( restart_needed )); then
      restart_gateways || failures+=("gateway-restart")
    fi
    if ((${#failures[@]})); then
      log "incomplete: ${failures[*]}"
    fi
    # 전량 재판정 — 여기가 하드 게이트다. 위 실행이 몇 개 성공했든 rc 는 이 판정이 정한다.
    if probe report; then
      write_receipt
      exit $?
    fi
    log "재판정 실패 — 영수증 없음"
    exit 1
    ;;
  *)
    echo "usage: deploy_all.sh [--plan|--verify|--apply [--wait-converge]]" >&2
    exit 2
    ;;
esac
