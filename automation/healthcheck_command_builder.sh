#!/usr/bin/env bash

if [[ -z "${HEALTHCHECK_COMMAND_BUILDER_LOADED:-}" ]]; then
  readonly HEALTHCHECK_COMMAND_BUILDER_LOADED=1
  readonly HEALTHCHECK_REPAIR_SECURE_PATH="${HEALTHCHECK_REPAIR_SECURE_PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
  readonly HEALTHCHECK_REPAIR_AGENT_UID="${HEALTHCHECK_REPAIR_AGENT_UID:-1002}"
fi

# detect 는 불변 릴리스 런타임의 사본만 실행한다. 계정 홈 사본은 배포 선언에도 드리프트
# 프로브의 홈 패턴에도 없어 조용히 낡는데, 이 경로가 가장 많이 도는 detect 라 2026-09-09 실측에서
# 종결 카드 하나에 재발이 2,829회 쌓여 있었다. 이 문자열은 SSH 강제명령 allowlist 에 sha256 으로
# 박히므로 바꾸면 소유자가 automation/provision-healthcheck-probe.sh 를 다시 돌려야 한다.
healthcheck_repair_command() {
  local check_name="$1"
  [[ "$HEALTHCHECK_REPAIR_AGENT_UID" =~ ^[0-9]+$ ]] || return 1
  [[ "$HEALTHCHECK_REPAIR_SECURE_PATH" =~ ^(/[A-Za-z0-9._/-]+)(:/[A-Za-z0-9._/-]+)*$ ]] || return 1
  [[ -n "${NODE_RELEASE_CURRENT:-}" ]] || return 1
  printf "sudo -n -u %s -H env PATH=%s/.local/bin:%s XDG_RUNTIME_DIR=/run/user/%s /usr/bin/python3 -I %s/automation/repair/repair_cli.py detect --source healthcheck --location '%s' --stdin" \
    "$NODE_AGENT_ACCOUNT" "$NODE_AGENT_HOME" \
    "$HEALTHCHECK_REPAIR_SECURE_PATH" "$HEALTHCHECK_REPAIR_AGENT_UID" "$NODE_RELEASE_CURRENT" "$check_name"
}
