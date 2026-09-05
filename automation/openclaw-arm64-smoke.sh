#!/usr/bin/env bash
# =============================================================================
# automation/openclaw-arm64-smoke.sh — plan task W0-9, OpenClaw ARM64 fallback
#
# Stage 1 (implemented): install OpenClaw for ops, configure exactly two small
# agents using Codex OAuth, prove one routed reply, save synthetic evidence,
# then leave every OpenClaw user service inactive and disabled.
#
# Stage 2 is intentionally a guarded stub for the later W1 checkpoint. It
# must preserve the same fail-closed Codex OAuth provider contract.
#
# Run on the configured primary node as the infrastructure account:
#   sudo -u ops -H bash /srv/autophagy-agents/automation/openclaw-arm64-smoke.sh --stage 1
# =============================================================================
set -Eeuo pipefail

readonly OPS_USER="ops"
readonly STAGE1_GATEWAY_PORT="18889"
readonly REPO_DIR="/srv/autophagy-agents"
readonly QA_DIR="${REPO_DIR}/docs/qa/W0-9"
readonly NODE_VERSION="v24.18.0"
readonly NODE_ARCHIVE="node-${NODE_VERSION}-linux-arm64.tar.xz"
readonly NODE_SHA256="58c9520501f6ae2b52d5b210444e24b9d0c029a58c5011b797bc1fe7105886f6"
readonly NODE_INSTALL_DIR="${HOME}/.local/${NODE_ARCHIVE%.tar.xz}"
readonly NODE_BIN_DIR="${NODE_INSTALL_DIR}/bin"
readonly USER_BIN_DIR="${HOME}/.local/bin"
readonly STATE_DIR="${HOME}/.local/state/openclaw-arm64-smoke"
readonly SENTINEL="${STATE_DIR}/stage1-onboarded"
readonly ROUTER_AGENT="router"
readonly WORKER_AGENT="worker"

export PATH="${NODE_BIN_DIR}:${USER_BIN_DIR}:${PATH}"

STAGE=""
INSTALL_METHOD="auto"
GATEWAY_STARTED=0
TRANSCRIPT=""

log()  { printf '[openclaw-smoke] %s\n' "$*"; }
warn() { printf '[openclaw-smoke] WARN: %s\n' "$*" >&2; }
die()  { printf '[openclaw-smoke] ERROR: %s\n' "$*" >&2; exit 1; }
banner() { printf '\n===== %s =====\n' "$*"; }
trap 'printf "[openclaw-smoke] FAILED at line %s: %s\n" "$LINENO" "$BASH_COMMAND" >&2' ERR

usage() {
  cat >&2 <<'EOF'
Usage: openclaw-arm64-smoke.sh --stage <1|2> [--install-method <auto|npm>]

  --stage 1              Run the implemented ARM64 two-agent smoke test.
  --stage 2              Reserved for the later W1 Discord checkpoint.
  --install-method auto  Use the official Linux installer (default).
  --install-method npm   Same installer, explicitly requesting its npm path.

If the official installer fails on aarch64, this script records a Docker
fallback marker and exits 75. Follow docs/guide/w0-9-openclaw-smoke.md §6;
the Docker retry is intentionally a human-visible branch, not a silent
best-effort replacement.
EOF
  exit 2
}

require_cmds() {
  local command_name
  for command_name in "$@"; do
    command -v "$command_name" >/dev/null 2>&1 || die "required command not found: $command_name"
  done
}

install_node_if_absent() {
  local temp_dir archive_path
  if [[ -x "${NODE_BIN_DIR}/node" ]]; then
    [[ "$("${NODE_BIN_DIR}/node" --version)" == "$NODE_VERSION" ]] || \
      die "managed Node.js at ${NODE_BIN_DIR} is not ${NODE_VERSION}; replace it deliberately rather than mixing runtimes"
    require_cmds node npm npx
    log "Node.js: $(node --version) — preserving user-space install at ${NODE_INSTALL_DIR}"
    return 0
  fi

  require_cmds curl tar sha256sum mktemp
  banner "[1/8] user-space Node.js ${NODE_VERSION} install (linux-arm64)"
  install -d -m 700 "${HOME}/.local" "$USER_BIN_DIR"
  [[ ! -e "$NODE_INSTALL_DIR" ]] || die "refusing to overwrite incomplete or operator-owned Node.js directory: $NODE_INSTALL_DIR"

  temp_dir="$(mktemp -d)"
  archive_path="${temp_dir}/${NODE_ARCHIVE}"
  if ! curl -fsSL "https://nodejs.org/dist/${NODE_VERSION}/${NODE_ARCHIVE}" -o "$archive_path"; then
    rm -rf -- "$temp_dir"
    die "could not download official Node.js archive: ${NODE_ARCHIVE}"
  fi
  if ! printf '%s  %s\n' "$NODE_SHA256" "$NODE_ARCHIVE" | (cd "$temp_dir" && sha256sum -c -); then
    rm -rf -- "$temp_dir"
    die "Node.js archive checksum verification failed: ${NODE_ARCHIVE}"
  fi
  if ! tar -xJf "$archive_path" -C "${HOME}/.local"; then
    rm -rf -- "$temp_dir"
    die "could not extract Node.js archive: ${NODE_ARCHIVE}"
  fi
  rm -rf -- "$temp_dir"

  [[ -x "${NODE_BIN_DIR}/node" ]] || die "Node.js archive did not provide ${NODE_BIN_DIR}/node"
  for command_name in node npm npx corepack; do
    if [[ ! -e "${USER_BIN_DIR}/${command_name}" || -L "${USER_BIN_DIR}/${command_name}" ]]; then
      ln -sfn "${NODE_BIN_DIR}/${command_name}" "${USER_BIN_DIR}/${command_name}"
    fi
  done
  require_cmds node npm npx
  [[ "$(node --version)" == "$NODE_VERSION" ]] || die "unexpected Node.js version after install: $(node --version)"
  log "Node.js installed: $(node --version) at ${NODE_INSTALL_DIR} (no sudo, no system-wide files)"
}

require_ops_context() {
  [[ "$(id -un)" == "$OPS_USER" ]] || die "must run as $OPS_USER (use: sudo -u ops -H bash ... )"
  [[ "$HOME" == "/home/${OPS_USER}" ]] || die "expected HOME=/home/${OPS_USER}; got HOME=$HOME"
  [[ -d "$REPO_DIR/.git" ]] || die "expected ops checkout not found: $REPO_DIR"
}

runtime_dir() {
  printf '/run/user/%s\n' "$(id -u)"
}

user_systemctl() {
  XDG_RUNTIME_DIR="$(runtime_dir)" systemctl --user "$@"
}

assert_stage1_port_free() {
  case "$STAGE1_GATEWAY_PORT" in
    4000|8800|9119) die "refusing reserved autophagy port: $STAGE1_GATEWAY_PORT" ;;
  esac

  if ss -ltn "sport = :${STAGE1_GATEWAY_PORT}" | grep -q ":${STAGE1_GATEWAY_PORT}"; then
    die "OpenClaw smoke port ${STAGE1_GATEWAY_PORT} is already listening; inspect with ss -tlnp before retrying"
  fi
  log "port ${STAGE1_GATEWAY_PORT}: free (not reserved: 4000/8800/9119)"
}

write_docker_fallback_marker() {
  local installer_status="$1" marker
  install -d -m 700 "$STATE_DIR"
  install -d -m 2750 "$QA_DIR"
  marker="${QA_DIR}/90-docker-fallback-required.txt"
  cat > "$marker" <<EOF
W0-9 stage-1 official OpenClaw Linux installer failed on aarch64.
installer_exit=${installer_status}
No Docker command was run automatically. Follow docs/guide/w0-9-openclaw-smoke.md §6,
capture the Docker retry output as 90-docker-fallback-attempt.txt, verify the resulting
openclaw command is available to ops, then re-run this script with --stage 1.
EOF
  chmod 640 "$marker"
  warn "installer failed; wrote $marker"
}

install_openclaw_if_absent() {
  local installer_status
  if command -v openclaw >/dev/null 2>&1; then
    log "OpenClaw: $(openclaw --version 2>/dev/null || printf 'installed') — preserving existing install"
    return 0
  fi

  require_cmds curl bash
  banner "[2/8] official OpenClaw Linux install (${INSTALL_METHOD})"
  set +e
  if [[ "$INSTALL_METHOD" == "npm" ]]; then
    curl -fsSL https://openclaw.ai/install.sh | bash -s -- --install-method npm
  else
    curl -fsSL https://openclaw.ai/install.sh | bash
  fi
  installer_status=$?
  set -e
  if [[ "$installer_status" -ne 0 ]]; then
    write_docker_fallback_marker "$installer_status"
    exit 75
  fi

  export PATH="${NODE_BIN_DIR}:${USER_BIN_DIR}:${PATH}"
  command -v openclaw >/dev/null 2>&1 || die "official installer exited 0 but openclaw is not on ops PATH; open a fresh shell or fix ~/.local/bin PATH"
  log "OpenClaw installed: $(openclaw --version 2>/dev/null || printf 'version unavailable')"
}

onboard_once() {
  install -d -m 700 "$STATE_DIR"
  if [[ -f "$SENTINEL" ]]; then
    log "onboarding: stage-1 sentinel exists — preserving current OpenClaw setup"
    return 0
  fi

  [[ ! -e "$HOME/.openclaw/openclaw.json" ]] || \
    die "existing $HOME/.openclaw/openclaw.json is operator-owned; refuse to replace its provider"

  openclaw onboard --non-interactive --accept-risk \
    --mode local \
    --auth-choice openai-codex \
    --gateway-bind loopback \
    --install-daemon
  install -m 600 /dev/null "$SENTINEL"
  log "onboarding: completed (sentinel written; future re-runs do not re-onboard)"
}

configure_exactly_two_agents() {
  local agents_json
  agents_json=$(cat <<EOF
[{"id":"${ROUTER_AGENT}","name":"Router","default":true,"workspace":"${HOME}/.openclaw/workspace-${ROUTER_AGENT}"},{"id":"${WORKER_AGENT}","name":"Worker","workspace":"${HOME}/.openclaw/workspace-${WORKER_AGENT}"}]
EOF
)
  install -d -m 700 "$HOME/.openclaw/workspace-${ROUTER_AGENT}" "$HOME/.openclaw/workspace-${WORKER_AGENT}"

  # `config set` accepts scalar or JSON object/array values. These writes are
  # intentionally convergent: every re-run restores this isolated W0-9 shape.
  openclaw config set gateway.bind loopback
  openclaw config set gateway.port "$STAGE1_GATEWAY_PORT"
  openclaw config set agents.defaults.model.primary openai-codex/gpt-5.6-sol
  openclaw config set agents.list "$agents_json" --strict-json --replace
  openclaw config set tools.profile messaging
  openclaw config set tools.sessions.visibility all
  openclaw config set tools.agentToAgent.enabled true
  openclaw config set tools.agentToAgent.allow "[\"${ROUTER_AGENT}\",\"${WORKER_AGENT}\"]"
  openclaw config set session.agentToAgent.maxPingPongTurns 0
  openclaw config set transcripts.enabled true
  openclaw config set transcripts.maxUtterances 2000
  openclaw gateway install --force --port "$STAGE1_GATEWAY_PORT"
  log "configuration: exactly two agents (${ROUTER_AGENT}, ${WORKER_AGENT}); Codex OAuth; no channel bindings"
}

start_gateway() {
  local ready_attempt
  banner "[3/8] start temporary loopback gateway (:${STAGE1_GATEWAY_PORT})"
  user_systemctl daemon-reload
  user_systemctl enable openclaw-gateway.service
  user_systemctl restart openclaw-gateway.service
  user_systemctl is-active --quiet openclaw-gateway.service || die "openclaw-gateway.service did not become active"
  for ((ready_attempt = 1; ready_attempt <= 30; ready_attempt++)); do
    if curl -fsS "http://127.0.0.1:${STAGE1_GATEWAY_PORT}/readyz" >/dev/null; then
      GATEWAY_STARTED=1
      log "gateway: active and ready temporarily"
      return 0
    fi
    sleep 1
  done
  die "openclaw-gateway.service did not become ready on port ${STAGE1_GATEWAY_PORT}"
}

stop_and_disable_gateway() {
  if [[ "$GATEWAY_STARTED" -eq 1 ]] || user_systemctl list-unit-files --no-legend 'openclaw*.service' 2>/dev/null | grep -q '^openclaw'; then
    # This script is already executing as ops. It is the inner command in the
    # plan-standard wrapper `sudo -u ops XDG_RUNTIME_DIR=... systemctl --user`;
    # nested sudo would incorrectly prompt for ops's password.
    user_systemctl disable --now openclaw-gateway.service >/dev/null 2>&1 || true
    log "gateway: stopped and disabled; install/config preserved"
  fi
}

cleanup() {
  local status=$?
  stop_and_disable_gateway
  return "$status"
}

run_stage1_round_trip() {
  local worker_session router_session worker_raw router_raw nonce expected_marker
  banner "[4/8] two-agent routed exchange"
  install -d -m 2750 "$QA_DIR"
  TRANSCRIPT="${QA_DIR}/01-stage1-two-agent-transcript.txt"
  worker_raw="${STATE_DIR}/worker-bootstrap.json"
  router_raw="${STATE_DIR}/router-roundtrip.json"
  worker_session="w0-9-stage1-worker"
  router_session="w0-9-stage1-router"
  nonce="$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')"
  [[ "${#nonce}" -eq 24 ]] || die "could not generate a 96-bit worker nonce"
  expected_marker="W0-9-WORKER-REPLY:${nonce}"

  # Seed a named worker session. Router can discover it through sessions_list
  # because tools.sessions.visibility=all, then use sessions_send to delegate.
  openclaw agent --agent "$WORKER_AGENT" --session-id "$worker_session" --json \
    --message "Reply exactly W0-9-WORKER-READY. For a later message beginning W0-9-ROUTE, reply exactly ${expected_marker}." \
    > "$worker_raw"

  openclaw agent --agent "$ROUTER_AGENT" --session-id "$router_session" --json \
    --message "Use OpenClaw sessions_list to locate the ${WORKER_AGENT} session named ${worker_session}. Use sessions_send to send it: W0-9-ROUTE. Wait for its reply. Your final answer must relay the worker's exact reply verbatim and state that it came from ${WORKER_AGENT}." \
    > "$router_raw"

  {
    printf 'W0-9 stage-1 synthetic two-agent transcript\n'
    printf 'utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'gateway=127.0.0.1:%s\n' "$STAGE1_GATEWAY_PORT"
    printf 'provider=Codex OAuth (no alternate provider, no Discord)\n'
    printf 'agents=%s -> %s\n\n' "$ROUTER_AGENT" "$WORKER_AGENT"
    printf '%s\n' '===== worker bootstrap (OpenClaw JSON event stream) ====='
    cat "$worker_raw"
    printf '\n%s\n' '===== router delegation round-trip (OpenClaw JSON event stream) ====='
    cat "$router_raw"
    printf '\n%s\n' '===== OpenClaw transcript exports (best effort) ====='
    openclaw transcripts show "$worker_session" 2>&1 || true
    openclaw transcripts show "$router_session" 2>&1 || true
  } > "$TRANSCRIPT"
  chmod 640 "$TRANSCRIPT"

  grep -Fq "$expected_marker" "$router_raw" || die "nonce-bearing worker reply missing from router JSON response; inspect $TRANSCRIPT"
  printf '\nrouter_nonce_assertion=PASS (nonce-bearing worker reply present in router JSON)\n' >> "$TRANSCRIPT"
  log "two-agent round trip: PASS ($TRANSCRIPT)"
}

write_inactive_status() {
  local status_file
  status_file="${QA_DIR}/02-stage1-services-inactive.txt"
  if user_systemctl is-active --quiet openclaw-gateway.service; then
    die "post-cleanup check failed: openclaw-gateway.service is still active"
  fi
  {
    printf 'W0-9 stage-1 post-cleanup service check\n'
    printf 'utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    user_systemctl status 'openclaw*' --no-pager 2>&1 || true
    printf '\nPASS openclaw services inactive after stage-1 smoke\n'
  } > "$status_file"
  chmod 640 "$status_file"
  log "inactive evidence: $status_file"
}

run_stage2_stub() {
  cat >&2 <<'EOF'
Stage 2 is intentionally not implemented in W0-9 stage 1.
The later W1 checkpoint must add test peer bot smoke evidence under docs/qa/W0-9/stage2/
while preserving the stage-1 installation and Codex OAuth config.
EOF
  exit 64
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --stage) STAGE="${2:-}"; shift 2 ;;
    --install-method) INSTALL_METHOD="${2:-}"; shift 2 ;;
    -h|--help) usage ;;
    *) die "unknown argument: $1" ;;
  esac
done

case "$STAGE" in
  1|2) ;;
  *) usage ;;
esac
case "$INSTALL_METHOD" in
  auto|npm) ;;
  *) die "unsupported --install-method '$INSTALL_METHOD' (supported: auto, npm)" ;;
esac

if [[ "$STAGE" == "2" ]]; then
  run_stage2_stub
fi

require_cmds id stat ss grep systemctl install od tr curl sleep
require_ops_context
assert_stage1_port_free
install_node_if_absent
install_openclaw_if_absent
trap cleanup EXIT
onboard_once
configure_exactly_two_agents
start_gateway
run_stage1_round_trip
stop_and_disable_gateway
GATEWAY_STARTED=0
write_inactive_status
log "DONE stage 1 — copy only 01-stage1-two-agent-transcript.txt and 02-stage1-services-inactive.txt into the local docs/qa/W0-9/"
