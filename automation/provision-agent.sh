#!/usr/bin/env bash
set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
eval "$(python3 "$REPO_ROOT/automation/node_config_sh.py" --print-env)"
SERVICE_NAME="$NODE_AGENT_GATEWAY_UNIT"
readonly HERMES_INSTALLER_URL="https://hermes-agent.nousresearch.com/install.sh"
readonly LITELLM_BASE_URL="http://127.0.0.1:4000/v1"
readonly LITELLM_MODEL="glm-main"
readonly FALLBACK_PROVIDER="openai-codex"
readonly FALLBACK_MODEL="gpt-5.3-codex"

log() {
  printf '[provision-agent] %s\n' "$*"
}

die() {
  printf '[provision-agent] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat >&2 <<'EOF'
Usage: provision-agent.sh <account>

Provision the named existing Linux account with the W1-2 Phase-A Hermes
configuration. The account must already own a mode-0600 ~/.env.secrets file
containing DISCORD_BOT_TOKEN and its account-specific LiteLLM key.
EOF
  exit 2
}

require_commands() {
  local command_name
  for command_name in "$@"; do
    command -v "$command_name" >/dev/null 2>&1 || die "required command not found: $command_name"
  done
}

run_as_account() {
  sudo -n -u "$ACCOUNT" -H env \
    "HOME=$ACCOUNT_HOME" \
    "PATH=$ACCOUNT_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" \
    "$@"
}

user_systemctl() {
  run_as_account env "XDG_RUNTIME_DIR=$USER_RUNTIME_DIR" systemctl --user "$@"
}

required_secret_is_present() {
  local variable_name="$1"
  local pattern

  # Only validate the variable name and the presence of a non-empty value. The
  # file is never sourced or printed, preventing credential expansion or leaks.
  pattern="^[[:space:]]*(export[[:space:]]+)?${variable_name}=[\"']?[^[:space:]\"']"
  run_as_account grep -Eq "$pattern" "$SECRETS_FILE"
}

validate_account_and_prerequisites() {
  local account_record mode owner variable_name

  [[ "$ACCOUNT" =~ ^[a-z_][a-z0-9_-]*$ ]] || die "invalid account name: '$ACCOUNT'"

  if ! account_record="$(getent passwd "$ACCOUNT")"; then
    die "account '$ACCOUNT' does not exist (getent passwd '$ACCOUNT' returned no entry)"
  fi

  ACCOUNT_HOME="$(printf '%s\n' "$account_record" | cut -d: -f6)"
  [[ -n "$ACCOUNT_HOME" && -d "$ACCOUNT_HOME" ]] || \
    die "account '$ACCOUNT' has no usable home directory: '${ACCOUNT_HOME:-<empty>}'"

  ACCOUNT_UID="$(id -u "$ACCOUNT")"
  USER_RUNTIME_DIR="/run/user/$ACCOUNT_UID"

  sudo -n -u "$ACCOUNT" -H true >/dev/null 2>&1 || \
    die "cannot run commands as '$ACCOUNT' without a password; grant the caller sudo -n -u $ACCOUNT or run as root"

  SECRETS_FILE="$ACCOUNT_HOME/.env.secrets"
  run_as_account test -f "$SECRETS_FILE" || \
    die "required secrets file is missing: $SECRETS_FILE (populate it out-of-band before provisioning)"

  mode="$(run_as_account stat -c '%a' "$SECRETS_FILE")"
  owner="$(run_as_account stat -c '%U' "$SECRETS_FILE")"
  [[ "$mode" == "600" && "$owner" == "$ACCOUNT" ]] || \
    die "$SECRETS_FILE must be owned by '$ACCOUNT' and mode 600 (found owner=$owner mode=$mode)"
  run_as_account test -r "$SECRETS_FILE" || die "$SECRETS_FILE is not readable by '$ACCOUNT'"

  for variable_name in DISCORD_BOT_TOKEN "$LITELLM_KEY_ENV"; do
    required_secret_is_present "$variable_name" || \
      die "$SECRETS_FILE is missing a non-empty $variable_name entry"
  done

  HERMES_HOME="$ACCOUNT_HOME/.hermes"
  HERMES_SOURCE_DIR="$HERMES_HOME/hermes-agent"
  CONFIG_FILE="$HERMES_HOME/config.yaml"
  DROPIN_DIR="$ACCOUNT_HOME/.config/systemd/user/${SERVICE_NAME}.d"
  DROPIN_FILE="$DROPIN_DIR/10-env-secrets.conf"
  DROPIN_SYNC_FILE="$DROPIN_DIR/30-command-sync.conf"
}

ensure_linger() {
  local linger

  linger="$(loginctl show-user "$ACCOUNT" -p Linger --value)" || \
    die "could not determine linger state for '$ACCOUNT'"
  if [[ "$linger" != "yes" ]]; then
    sudo -n loginctl enable-linger "$ACCOUNT" || \
      die "could not enable linger for '$ACCOUNT'"
    linger="$(loginctl show-user "$ACCOUNT" -p Linger --value)"
  fi
  [[ "$linger" == "yes" ]] || die "linger is not enabled for '$ACCOUNT'"
  [[ -d "$USER_RUNTIME_DIR" ]] || \
    die "runtime directory missing after enabling linger: $USER_RUNTIME_DIR"
  log "linger: enabled for '$ACCOUNT'"
}

hermes_install_is_valid() {
  run_as_account test -d "$HERMES_SOURCE_DIR" || return 1
  run_as_account bash -c 'command -v hermes >/dev/null 2>&1 && hermes --version >/dev/null 2>&1'
}

install_hermes_if_needed() {
  if hermes_install_is_valid; then
    log "Hermes install: valid existing install at $HERMES_SOURCE_DIR — skipping"
    return
  fi

  log "Hermes install: running the official installer for '$ACCOUNT'"
  run_as_account bash -c "set -euo pipefail; curl -fsSL '$HERMES_INSTALLER_URL' | bash"
  hermes_install_is_valid || die "official installer completed but no valid Hermes install was found for '$ACCOUNT'"
  log "Hermes install: complete"
}

render_config() {
  cat <<EOF
model:
  provider: custom:litellm
  default: ${LITELLM_MODEL}

custom_providers:
  - name: litellm
    base_url: ${LITELLM_BASE_URL}
    key_env: ${LITELLM_KEY_ENV}
    api_mode: chat_completions
    default_model: ${LITELLM_MODEL}
EOF

  if [[ "$ACCOUNT" == "$NODE_AGENT_ACCOUNT" ]]; then
    cat <<EOF

fallback_providers:
  - provider: ${FALLBACK_PROVIDER}
    model: ${FALLBACK_MODEL}
EOF
  fi

  cat <<'EOF'

# Non-secret approval-reminder policy. Omit this section on older deployments
# to retain the same defaults.
approval_reminders:
  enabled: true
  initial_delay: 3h
  repeat_interval: 1h
EOF
}

render_dropin() {
  cat <<EOF
[Service]
EnvironmentFile=${SECRETS_FILE}
EOF
}

# WHY bulk (2026-08-22): Hermes 0.20.3의 기본(safe) sync는 커맨드 diff를 건당
# mutation으로 보낸다. 업스트림 업데이트로 커맨드 전부가 한꺼번에 달라지면 Discord의
# 작은 command 버킷 안에서 한 번에 끝나지 못해 429로 끊기고, 성공 기록이 남지 않아
# 매 부팅 재시도한다 — 배포 재시동이 잦은 날은 그 루프가 앱 전체를 429 패널티 창에
# 가둬 승인 요청의 auto-thread 생성까지 함께 죽었다(01:32 KST 사건). bulk는 diff
# 크기와 무관하게 부팅당 PUT 1회라 그 실패 계열 전체에 면역이다.
render_command_sync_dropin() {
  cat <<'EOF'
[Service]
Environment=DISCORD_COMMAND_SYNC_POLICY=bulk
EOF
}

ensure_file_if_absent() {
  local expected_file="$1"
  local destination="$2"
  local destination_dir="$3"
  local label="$4"

  if run_as_account test -e "$destination"; then
    if run_as_account cmp -s "$expected_file" "$destination"; then
      log "$label: already matches — skipping"
    else
      log "$label: already set — preserving (only-if-unset)"
    fi
    return
  fi

  run_as_account install -d -m 700 "$destination_dir"
  run_as_account install -m 600 "$expected_file" "${destination}.tmp"
  run_as_account mv -f "${destination}.tmp" "$destination"
  FILE_CHANGED=1
  log "$label: written"
}

ensure_initial_config_and_dropin() {
  local desired_config="$WORK_DIR/config.yaml"
  local desired_dropin="$WORK_DIR/autophagy-env.conf"
  local desired_sync_dropin="$WORK_DIR/autophagy-command-sync.conf"

  render_config > "$desired_config"
  render_dropin > "$desired_dropin"
  render_command_sync_dropin > "$desired_sync_dropin"
  chmod 644 "$desired_config" "$desired_dropin" "$desired_sync_dropin"

  ensure_file_if_absent "$desired_config" "$CONFIG_FILE" "$HERMES_HOME" "Hermes config"
  ensure_file_if_absent "$desired_dropin" "$DROPIN_FILE" "$DROPIN_DIR" "systemd EnvironmentFile drop-in"
  ensure_file_if_absent "$desired_sync_dropin" "$DROPIN_SYNC_FILE" "$DROPIN_DIR" "systemd command-sync drop-in"
}

ensure_gateway_service() {
  local service_was_present=0

  if user_systemctl cat "$SERVICE_NAME" >/dev/null 2>&1; then
    service_was_present=1
    log "gateway unit: already installed — skipping Hermes unit generation"
  else
    log "gateway unit: installing Hermes user service"
    run_as_account env "XDG_RUNTIME_DIR=$USER_RUNTIME_DIR" hermes gateway install --force --start-now
    SERVICE_CHANGED=1
  fi

  if (( FILE_CHANGED || SERVICE_CHANGED )); then
    user_systemctl daemon-reload
    log "gateway unit: daemon-reload complete"
  fi

  if ! user_systemctl is-enabled --quiet "$SERVICE_NAME"; then
    user_systemctl enable "$SERVICE_NAME"
    SERVICE_CHANGED=1
    log "gateway unit: enabled"
  else
    log "gateway unit: already enabled — skipping"
  fi

  if (( FILE_CHANGED && service_was_present )); then
    user_systemctl restart "$SERVICE_NAME"
    log "gateway unit: restarted after configuration change"
  elif ! user_systemctl is-active --quiet "$SERVICE_NAME"; then
    user_systemctl start "$SERVICE_NAME"
    log "gateway unit: started"
  else
    log "gateway unit: already active — skipping"
  fi
}

verify_gateway_service() {
  local status

  if ! status="$(user_systemctl is-active "$SERVICE_NAME" 2>/dev/null)"; then
    die "$SERVICE_NAME is not active for '$ACCOUNT'"
  fi
  [[ "$status" == "active" ]] || die "$SERVICE_NAME is not active for '$ACCOUNT' (status=$status)"
  log "verification: $SERVICE_NAME is active for '$ACCOUNT'"
}

# 소유자의 자격증명 조회 alias는 `~<operator>/.bash_aliases`에만 있어 노드를
# 재구축하면 **조용히** 사라졌다 — 자격증명 자체는 mode-600 파일에 남아 있으므로
# 아무것도 실패하지 않고 그저 꺼내 볼 수만 없게 된다. alias에는 비밀이 없다 —
# `sudo -n -u <account> cat <path>` 조회 명령일 뿐이다. 소유자가 자기 dotfile을
# 고쳐둔 경우를 위해 마커가 없을 때만 덧붙인다(only-if-absent).
ensure_operator_credential_aliases() {
  local target="$(getent passwd "$NODE_OPERATOR_ACCOUNT" | cut -d: -f6)/.bash_aliases"

  if [[ -z "$NODE_OPERATOR_ACCOUNT" ]] || ! getent passwd "$NODE_OPERATOR_ACCOUNT" >/dev/null; then
    log "operator aliases: '$NODE_OPERATOR_ACCOUNT' 계정이 없다 — 건너뜀"
    return
  fi
  if sudo -n -u "$NODE_OPERATOR_ACCOUNT" grep -qs 'autophagy-cred-alias' "$target"; then
    log "operator aliases: already defined — skipping"
    return
  fi
  sudo -n -u "$NODE_OPERATOR_ACCOUNT" tee -a "$target" >/dev/null <<EOF
# autophagy-cred-alias — 대시보드 Basic auth 조회 (owner 전용, 값은 mode-600 파일에만 있다)
alias kanban-cred="sudo -n -u $NODE_AGENT_ACCOUNT cat $NODE_AGENT_HOME/.hermes/dashboard-cha-credentials.txt"
alias reporthub-cred="sudo -n -u $NODE_OPS_ACCOUNT cat $NODE_OPS_HOME/report-hub/dashboard-cha-credentials.txt"
alias autophagy-cred="kanban-cred; echo; reporthub-cred"
EOF
  log "operator aliases: restored in $target"
}

[[ "$#" -eq 1 ]] || usage
ACCOUNT="$1"
[[ -n "$ACCOUNT" ]] || usage
if [[ "$ACCOUNT" == "$NODE_PEER_ACCOUNT" ]]; then
  SERVICE_NAME="$NODE_PEER_GATEWAY_UNIT"
fi

ACCOUNT_ENV_SUFFIX="$(printf '%s' "$ACCOUNT" | LC_ALL=C tr '[:lower:]' '[:upper:]' | tr -c 'A-Z0-9_' '_')"
LITELLM_KEY_ENV="LITELLM_${ACCOUNT_ENV_SUFFIX}_KEY"

ACCOUNT_HOME=""
ACCOUNT_UID=""
USER_RUNTIME_DIR=""
SECRETS_FILE=""
HERMES_HOME=""
HERMES_SOURCE_DIR=""
CONFIG_FILE=""
DROPIN_DIR=""
DROPIN_FILE=""
DROPIN_SYNC_FILE=""
FILE_CHANGED=0
SERVICE_CHANGED=0

require_commands bash chmod cmp curl cut getent grep id install loginctl mktemp mv rm stat sudo systemctl tee tr
validate_account_and_prerequisites
ensure_linger

WORK_DIR="$(mktemp -d)"
chmod 755 "$WORK_DIR"
trap 'rm -rf -- "$WORK_DIR"' EXIT

log "provisioning account '$ACCOUNT' with key variable $LITELLM_KEY_ENV"
ensure_initial_config_and_dropin
install_hermes_if_needed
ensure_gateway_service
verify_gateway_service
ensure_operator_credential_aliases
log "DONE: '$ACCOUNT' is provisioned; no OAuth or user DM test was attempted"
