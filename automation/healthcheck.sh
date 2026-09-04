#!/usr/bin/env bash
# Read-only liveness monitor for the deployed autophagy services.
#
# Run as the configured ops account. HEALTHCHECK_SSH_USER and HEALTHCHECK_SSH_IDENTITY
# default to the configured operator and ~/.ssh/autophagy-healthcheck (when the
# key is readable). It deliberately uses SSH plus read-only GET and systemctl
# --user is-active probes, so it remains independent of Hermes.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"; eval "$(python3 "$REPO_ROOT/automation/node_config_sh.py" --print-env)"
export RUNTIME_RELEASE_CURRENT="${RUNTIME_RELEASE_CURRENT:-$NODE_RELEASE_CURRENT}" HEALTHCHECK_RECONCILE_STATE="${HEALTHCHECK_RECONCILE_STATE:-$NODE_PRIVATE_ROOT/deploy-reconcile/state.json}"

# The deploy-checkout drift verdict + its recovery text live in a sibling library
# so this file stays under the 250 pure-LOC gate. It runs LOCALLY (no ssh/sudo).
# shellcheck source=automation/checkout_mirror_probe.sh
source "$(dirname "${BASH_SOURCE[0]}")/checkout_mirror_probe.sh"
# The skill-mount probe (verdict, recovery text, and the probe itself) lives in a
# sibling library for the same reason as the checkout one: this file must stay under
# the 250 pure-LOC gate. That library resolves the runtime root on its own.
# The skill-mount verdict + its recovery text live in a sibling library for the same
# reason as the checkout one: this file must stay under the 250 pure-LOC gate.
# shellcheck source=automation/skill_mount_probe.sh
source "$(dirname "${BASH_SOURCE[0]}")/skill_mount_probe.sh"; source "$(dirname "${BASH_SOURCE[0]}")/selfskill_root_probe.sh"
# shellcheck source=automation/release_store_probe.sh
source "$(dirname "${BASH_SOURCE[0]}")/release_store_probe.sh"
# shellcheck source=automation/release_helper_probe.sh
# shellcheck source=automation/watcher_drift_probe.sh
# shellcheck source=automation/healthcheck_wrapper_probe.sh
# shellcheck source=automation/runtime_package_probe.sh
source "$(dirname "${BASH_SOURCE[0]}")/release_helper_probe.sh"; source "$(dirname "${BASH_SOURCE[0]}")/watcher_drift_probe.sh"; source "$(dirname "${BASH_SOURCE[0]}")/healthcheck_wrapper_probe.sh"; source "$(dirname "${BASH_SOURCE[0]}")/runtime_package_probe.sh"; source "$(dirname "${BASH_SOURCE[0]}")/healthcheck_probe_evidence.sh"
# shellcheck source=automation/release_receipt_probe.sh
source "$(dirname "${BASH_SOURCE[0]}")/release_receipt_probe.sh"
# shellcheck source=automation/healthcheck_command_builder.sh
# shellcheck source=automation/healthcheck_validation.sh
# shellcheck source=automation/healthcheck_probes.sh
source "$(dirname "${BASH_SOURCE[0]}")/healthcheck_command_builder.sh"; source "$(dirname "${BASH_SOURCE[0]}")/healthcheck_validation.sh"; source "$(dirname "${BASH_SOURCE[0]}")/healthcheck_probes.sh"
# shellcheck source=automation/update_trust_probe.sh
# shellcheck source=automation/healthcheck_roster_probe.sh
source "$(dirname "${BASH_SOURCE[0]}")/update_trust_probe.sh"; source "$(dirname "${BASH_SOURCE[0]}")/healthcheck_roster_probe.sh"

readonly LOG_DIR="${HEALTHCHECK_LOG_DIR:-$NODE_PRIVATE_ROOT/runtime-logs/healthcheck}"
# 겹친 cron 틱이 서로를 보는 유일한 지점 — main() 의 양보 가드가 이 파일을 잡는다.
readonly LOCK_FILE="${HEALTHCHECK_LOCK_FILE:-$LOG_DIR/healthcheck.lock}"
readonly PRIMARY_NODE="$NODE_PRIMARY_NODE_NAME"
readonly RAG_NODE="$NODE_RAG_NODE_NAME"
if [[ -v HEALTHCHECK_SSH_IDENTITY ]]; then
  readonly SSH_IDENTITY="$HEALTHCHECK_SSH_IDENTITY"
elif [[ -r "$HOME/.ssh/autophagy-healthcheck" ]]; then
  readonly SSH_IDENTITY="$HOME/.ssh/autophagy-healthcheck"
else
  readonly SSH_IDENTITY=""
fi
readonly SSH_REMOTE_USER="${HEALTHCHECK_SSH_USER-$NODE_OPERATOR_ACCOUNT}"
SSH_OPTIONS=(
  -o BatchMode=yes
  -o ClearAllForwardings=yes
  -o ConnectTimeout=15
  -o StrictHostKeyChecking=yes
)
if [[ -n "$SSH_IDENTITY" ]]; then
  SSH_OPTIONS+=(-o IdentitiesOnly=yes -i "$SSH_IDENTITY")
fi
readonly -a SSH_OPTIONS

# Add an ordinary deployed service by adding one line here. Fields are:
# display name | probe type | node | account | target
readonly -a LIVE_CHECKS=(
  "$PRIMARY_NODE LiteLLM|http_200|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|http://127.0.0.1:4000/health/liveliness"
  "$PRIMARY_NODE LiteLLM completion|litellm_completion|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|http://127.0.0.1:4000"
  "$PRIMARY_NODE $NODE_AGENT_ACCOUNT $NODE_AGENT_GATEWAY_UNIT|user_unit_active|${PRIMARY_NODE}|$NODE_AGENT_ACCOUNT|$NODE_AGENT_GATEWAY_UNIT"
  "$PRIMARY_NODE $NODE_PEER_ACCOUNT $NODE_PEER_GATEWAY_UNIT|user_unit_active|${PRIMARY_NODE}|$NODE_PEER_ACCOUNT|$NODE_PEER_GATEWAY_UNIT"
  "$RAG_NODE embedding|embedding_health|${RAG_NODE}|$NODE_OPS_ACCOUNT|http://127.0.0.1:8001/health"
  "$RAG_NODE Qdrant|qdrant_health|${RAG_NODE}|$NODE_OPS_ACCOUNT|http://127.0.0.1:6333/healthz"
  "$RAG_NODE MCP|mcp_health|${RAG_NODE}|$NODE_OPS_ACCOUNT|http://127.0.0.1:8765/health"
  "$PRIMARY_NODE report-hub collector|user_unit_active|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|report-hub-collector.service"
  "$PRIMARY_NODE report-hub dashboard|user_unit_active|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|report-hub-dashboard.service"
  "$PRIMARY_NODE report-hub dashboard auth|http_unauth_401|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|http://100.116.248.95:8800/"
  "$PRIMARY_NODE signed update trust|update_trust|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_DEPLOY_CHECKOUT"
  "$PRIMARY_NODE ops checkout mirrors origin/main|checkout_mirrors_origin|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_DEPLOY_CHECKOUT"
  "$PRIMARY_NODE release matches origin/main|release_matches_origin|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_DEPLOY_CHECKOUT"
  "$PRIMARY_NODE privileged release helpers match release|release_helper_drift|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_LIBEXEC_DIR"
  "$PRIMARY_NODE skill mounts match the release|skill_mounts_current|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_SKILL_STORE/live"
  "$PRIMARY_NODE agent selfskill root topology|agent_selfskill_root_topology|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_SKILL_STORE/live"
  "$PRIMARY_NODE release store usage|release_store_usage|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_RELEASE_STORE"
  "$PRIMARY_NODE release fully deployed|release_fully_deployed|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|${HEALTHCHECK_DEPLOY_ALL_RECEIPT:-$NODE_PRIVATE_ROOT/deploy-all/receipt.json}"
  "$PRIMARY_NODE watcher wrappers match the release|watcher_wrappers_current|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|${HEALTHCHECK_WATCHER_MANIFEST:-$(dirname "${BASH_SOURCE[0]}")/../configs/watcher-deploy-manifest.txt}"
  "$PRIMARY_NODE runtime packages match the release|primary_runtime_packages_current|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|${HEALTHCHECK_RUNTIME_PACKAGE_MANIFEST:-$(dirname "${BASH_SOURCE[0]}")/../configs/runtime-package-manifest.txt}" "$RAG_NODE personal RAG source and MCP image match the release|rag_stack_current|${RAG_NODE}|$NODE_OPS_ACCOUNT|${HEALTHCHECK_RUNTIME_PACKAGE_MANIFEST:-$(dirname "${BASH_SOURCE[0]}")/../configs/runtime-package-manifest.txt}" "$PRIMARY_NODE healthcheck probe allowlist matches the checks|healthcheck_wrapper_current|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|automation/healthcheck_probe_wrapper.sh" "$RAG_NODE healthcheck probe allowlist matches the checks|healthcheck_wrapper_current|${RAG_NODE}|$NODE_OPS_ACCOUNT|automation/healthcheck_probe_wrapper.sh"
)

# Probes that run HERE, not over ssh. They must stay out of the remote tally: during a
# fleet-wide SSH outage they still pass, and counting them keeps the all-remote-down
# guard from collapsing N tickets into one INFRA_FAILURE (regression d7ed0ad / γ).
# One declaration on purpose — the same rule lived in two comparisons and the second
# copy is always the one that gets forgotten.
readonly LOCAL_PROBES="update_trust checkout_mirrors_origin release_matches_origin release_helper_drift skill_mounts_current agent_selfskill_root_topology release_store_usage release_fully_deployed"
UPDATE_TRUST_BLOCK_REPORTED=0
RELEASE_STALE_REPORTED=0

log() {
  printf '[healthcheck] %s\n' "$*"
}

usage() {
  cat >&2 <<'EOF'
Usage: healthcheck.sh [--synthetic-failure]

Without arguments, check the deployed services. --synthetic-failure performs
one read-only is-active probe for a deliberately nonexistent ops user unit; it
exists only to prove failure reporting without disrupting a real service.
EOF
  exit 2
}

require_commands() {
  local command_name
  for command_name in "$@"; do
    command -v "$command_name" >/dev/null 2>&1 || {
      printf '[healthcheck] ERROR: required command not found: %s\n' "$command_name" >&2
      exit 1
    }
  done
}

setup_log() {
  local timestamp

  umask 077
  mkdir -p -m 700 -- "$LOG_DIR"
  chmod 700 -- "$LOG_DIR"
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  LOG_FILE="${LOG_DIR}/healthcheck-${timestamp}.log"
  : > "$LOG_FILE"
  chmod 600 -- "$LOG_FILE"
  exec > >(tee -a "$LOG_FILE") 2>&1
  log "log=${LOG_FILE}"
}

# A repair ticket carries the check name, which is all an operator needs when the
# remedy is obvious (restart, re-auth). Deploy-checkout drift is the case where
# it is not: the commits stranded in the checkout exist nowhere else, so the
# reflexive repair - discard and realign - destroys them. Probe types without a
# rule here ship the name alone.
repair_guidance() {
  case "$1" in
    checkout_mirrors_origin)
      checkout_mirror_guidance "${HEALTHCHECK_OPS_CHECKOUT:-$NODE_DEPLOY_CHECKOUT}"
      ;;
    skill_mounts_current) skill_mount_guidance ;;
    agent_selfskill_root_topology) selfskill_root_guidance ;; release_store_usage) release_store_guidance ;;
    *) ;;
  esac
}

report_repair() {
  local check_name="$1"
  local probe_type="$2"
  local ssh_target="$PRIMARY_NODE" output repair_command command_status=0

  [[ "${HEALTHCHECK_NO_REPAIR:-}" != "1" ]] || return 0
  [[ "$check_name" =~ ^[a-zA-Z0-9_.@:/[:space:]-]+$ ]] || return 1
  [[ -z "$SSH_REMOTE_USER" ]] || ssh_target="${SSH_REMOTE_USER}@${PRIMARY_NODE}"
  repair_command="$(healthcheck_repair_command "$check_name")" || return 1
  output="$( { printf 'healthcheck failure: %s\n' "$check_name"; repair_guidance "$probe_type"; } | timeout 90 ssh "${SSH_OPTIONS[@]}" "$ssh_target" "$repair_command" 2>&1)" || command_status=$?
  if (( command_status == 0 )); then
    log "REPAIR_TICKET ${output}"
  else
    log "REPAIR_TICKET_FAILED rc=${command_status}"
  fi
}

# The deploy checkout is a one-way mirror of origin/main. This probe runs LOCALLY:
# healthcheck runs as ops on the primary node and the checkout is local there, so it
# needs neither ssh (allowlist-denied) nor sudo (sudoers-denied) - both rc=126. The
# verdict (clean/dirty/ahead/behind/unknown-remote) and the grading that turns it into
# pass/fail both live in checkout_mirror_probe.sh - this file is wiring, and grading a
# behind mirror needs what production runs, which is more than one line's worth.
# Read-only: it uses git ls-remote (no local ref written), never fetch/pull/reset.
# An unreachable origin degrades to a PASS + BEHIND-UNKNOWN, never a cry-wolf fail.
# probe_skill_mounts_current lives in skill_mount_probe.sh (LOC gate) — sourced above.

probe_litellm_completion() {
  local node="$1" account="$2" url="$3"
  local env_file="${HEALTHCHECK_LITELLM_ENV_FILE:-/home/ops/litellm-gateway/.env}"
  local script remote_command response verdict status error_type error_code extra
  valid_account "$account" && valid_http_url "${url}/" && valid_abs_path "$env_file" || return 1
  read -r -d '' script <<'REMOTE' || true
set -a; . "$1"; set +a
: "${LITELLM_MASTER_KEY:?}"
body=$(mktemp); trap 'rm -f -- "$body"' EXIT
curl_rc=0
status=$(curl --silent --max-time 20 --output "$body" --write-out '%{http_code}' --header 'Content-Type: application/json' --header "Authorization: Bearer ${LITELLM_MASTER_KEY}" --data '{"model":"glm-main","messages":[{"role":"user","content":"ping"}],"max_tokens":1}' "$2/chat/completions") || curl_rc=$?
python3 -c 'import json,re,sys
try: payload=json.load(open(sys.argv[3], encoding="utf-8"))
except (OSError, ValueError): payload={}
error=payload.get("error", {}) if isinstance(payload, dict) else {}
def field(name):
 value=error.get(name) if isinstance(error, dict) else None
 return re.sub(r"\s+", " ", str(value)) if isinstance(value, (str, int, float)) else "none"
choices=payload.get("choices") if isinstance(payload, dict) else None
healthy=sys.argv[1] == "0" and sys.argv[2] == "200" and isinstance(choices, list) and bool(choices)
print("PASS" if healthy else "FAIL", sys.argv[2] or "000", field("type"), field("code"), sep="\t")' "$curl_rc" "$status" "$body"
REMOTE
  printf -v remote_command 'sudo -n -u %s -H bash -o pipefail -c %q _ %q %q' "$account" "$script" "$env_file" "$url"
  if ! response="$(capture_on_node "$node" "$remote_command")"; then
    printf '[healthcheck] HTTP_STATUS=000 ERROR_TYPE=none ERROR_CODE=none\n' >&2; return 1
  fi
  IFS=$'\t' read -r verdict status error_type error_code extra <<< "$response"
  [[ "$verdict" == PASS && "$status" == 200 && -z "$extra" ]] && return 0
  printf '[healthcheck] HTTP_STATUS=%s ERROR_TYPE=%s ERROR_CODE=%s\n' "${status:-000}" "${error_type:-none}" "${error_code:-none}" >&2
  return 1
}

run_check() {
  local definition="$1"
  local check_name probe_type node account target

  IFS='|' read -r check_name probe_type node account target <<< "$definition"
  case "$probe_type" in
    http_200) probe_http_200 "$node" "$account" "$target" ;;
    litellm_completion) probe_litellm_completion "$node" "$account" "$target" ;;
    user_unit_active) probe_user_unit_active "$node" "$account" "$target" ;;
    http_unauth_401) probe_http_unauth_401 "$node" "$account" "$target" ;;
    embedding_health) probe_embedding_health "$node" "$account" "$target" ;;
    qdrant_health) probe_qdrant_health "$node" "$account" "$target" ;;
    mcp_health) probe_mcp_health "$node" "$account" "$target" ;;
    update_trust)
      probe_update_trust "$node" "$account" "$target" \
        || { UPDATE_TRUST_BLOCK_REPORTED=1; return 1; }
      ;;
    checkout_mirrors_origin) probe_checkout_mirrors_origin "$node" "$account" "$target" ;;
    release_matches_origin) probe_release_matches_origin "$node" "$account" "$target" ;;
    release_helper_drift) probe_release_helper_drift "$node" "$account" "$target" ;;
    skill_mounts_current) probe_skill_mounts_current "$node" "$account" "$target" ;;
    release_store_usage) probe_release_store_usage "$node" "$account" "$target" ;;
    release_fully_deployed) probe_release_fully_deployed "$node" "$account" "$target" ;;
    agent_selfskill_root_topology) probe_selfskill_root_topology "$node" "$account" "$target" ;;
watcher_wrappers_current) probe_watcher_wrappers_current "$node" "$account" "$target" ;; primary_runtime_packages_current) probe_primary_runtime_packages_current "$node" "$account" "$target" ;; rag_stack_current) probe_rag_stack_current "$node" "$account" "$target" ;;
healthcheck_wrapper_current) probe_healthcheck_wrapper_current "$node" "$account" "$target" ;;
    *) log "ERROR: ${check_name} has unsupported probe type ${probe_type}"; return 1 ;;
  esac
}

main() {
  local -a checks=("${LIVE_CHECKS[@]}")
  local definition check_name probe_type
  local -a failed_checks=()
  local remote_total=0 remote_failed=0

  case "${1:-}" in
    "") ;;
    --synthetic-failure)
      checks=("synthetic nonexistent ops unit|user_unit_active|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|autophagy-healthcheck-synthetic-does-not-exist.service")
      ;;
    *) usage ;;
  esac
  [[ "$#" -le 1 ]] || usage

  require_commands awk chmod date du flock grep mkdir mountpoint sed ssh stat tee timeout
  if [[ -n "$SSH_IDENTITY" && ! -r "$SSH_IDENTITY" ]]; then
    printf '[healthcheck] ERROR: SSH identity is not readable: %s\n' "$SSH_IDENTITY" >&2
    return 1
  fi

  # cron 은 이 sweep 을 */5 로 부르지만 최근 400 회 실행의 중앙값은 4048 초였다(p90 14760 초,
  # 최대 39020 초). 틱 간격보다 한 자릿수 길어 틱이 겹쳐 쌓였고, 2026-08-31 에 동시 실행
  # 114 개가 관측됐다. 그 폭주 아래에서 SSH 프로브가 간헐 타임아웃해 **거짓** 수리 티켓을
  # 냈다 — t_2578c8ed(LiteLLM)·t_2524fe33(peer 게이트웨이)는 16:30:02Z 에 시작한 실행이
  # 18:25:23Z 에 보고한 것이고, 같은 프로브는 다른 모든 실행에서 PASS 였다.
  # 그래서 겹친 틱은 sweep 을 **시작하지 않고** 양보한다: 양보는 실패가 아니라 다음 틱에
  # 넘기는 것이므로 rc 0 이다(automation/pipeline_lock.py 와 같은 규약). lock 을 열지도
  # 못하는 상태는 잡혀 있는 것과 구별할 수 없으므로 fail-closed 로 멈춘다.
  # fd 9 는 sweep 이 끝날 때까지 열어 둔다 — 닫는 순간 lock 이 풀려 겹침이 다시 열린다.
  mkdir -p -m 700 -- "$LOG_DIR"
  exec 9>"$LOCK_FILE" || {
    printf '[healthcheck] HEALTHCHECK-LOCK-UNAVAILABLE path=%s\n' "$LOCK_FILE" >&2
    return 1
  }
  flock -n 9 || {
    printf '[healthcheck] HEALTHCHECK-OVERLAP-SKIP a previous sweep still holds %s\n' "$LOCK_FILE" >&2
    return 0
  }
  setup_log
  log "mode=${1:-live} read_only=true"
  report_roster_identity

  for definition in "${checks[@]}"; do
    IFS='|' read -r check_name probe_type _ <<< "$definition"
    [[ " $LOCAL_PROBES " == *" $probe_type "* ]] || remote_total=$(( remote_total + 1 ))
    if healthcheck_run_check_with_evidence "$definition"; then
      log "PASS ${check_name}"
    else
      log "FAIL ${check_name}"
      failed_checks+=("$definition")
      [[ " $LOCAL_PROBES " == *" $probe_type "* ]] || remote_failed=$(( remote_failed + 1 ))
    fi
  done

  # Before the healthy early-return ON PURPOSE: the aggregator has to see a clean sweep to
  # close an open incident, so calling it only on failure would report every outage and
  # never a recovery. It self-loads its credential (cron, not systemd) and swallows its own
  # failures — a Discord outage must not change this sweep's verdict.
  "$(dirname "${BASH_SOURCE[0]}")/healthcheck_notify.sh" "${failed_checks[@]}"
  if (( ${#failed_checks[@]} == 0 )); then
    log "ALL_HEALTHY"
    return 0
  fi

  # Every SSH-borne probe rides one transport, so all of them failing is a single
  # transport/credential fault, not N outages - ticketing each would bury the cause
  # under identical noise on that same broken path. The local checkout probe is
  # excluded from this tally: it needs no SSH, so its verdict is independent and
  # must not mask (or be masked by) a fleet-wide SSH outage. >1 keeps a single
  # deliberately-failing --synthetic-failure check on the ticket path.
  if (( remote_failed == remote_total && remote_total > 1 )); then
    log "ERROR: every remote check failed - suspect the shared SSH path, not ${remote_total} separate services"
    log "ERROR: resolved HEALTHCHECK_SSH_USER=${SSH_REMOTE_USER:-<none>} HEALTHCHECK_SSH_IDENTITY=${SSH_IDENTITY:-<none>}"
    log "INFRA_FAILURE"
    return 1
  fi

  for definition in "${failed_checks[@]}"; do
    IFS='|' read -r check_name probe_type _ <<< "$definition"
    report_repair "$check_name" "$probe_type" || log "REPAIR_TICKET_FAILED validation"
  done

  log "HEALTHCHECK_FAILED"
  return 1
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
