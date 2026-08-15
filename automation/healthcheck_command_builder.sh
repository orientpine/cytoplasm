#!/usr/bin/env bash

if [[ -z "${HEALTHCHECK_COMMAND_BUILDER_LOADED:-}" ]]; then
  readonly HEALTHCHECK_COMMAND_BUILDER_LOADED=1
  readonly HEALTHCHECK_REPAIR_SECURE_PATH="${HEALTHCHECK_REPAIR_SECURE_PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
  readonly HEALTHCHECK_REPAIR_AGENT_UID="${HEALTHCHECK_REPAIR_AGENT_UID:-1002}"
fi

healthcheck_repair_command() {
  local check_name="$1"
  [[ "$HEALTHCHECK_REPAIR_AGENT_UID" =~ ^[0-9]+$ ]] || return 1
  [[ "$HEALTHCHECK_REPAIR_SECURE_PATH" =~ ^(/[A-Za-z0-9._/-]+)(:/[A-Za-z0-9._/-]+)*$ ]] || return 1
  printf "sudo -n -u %s -H env PATH=%s/.local/bin:%s XDG_RUNTIME_DIR=/run/user/%s /usr/bin/python3 -I %s/.hermes/repair/automation/repair/repair_cli.py detect --source healthcheck --location '%s' --stdin" \
    "$NODE_AGENT_ACCOUNT" "$NODE_AGENT_HOME" \
    "$HEALTHCHECK_REPAIR_SECURE_PATH" "$HEALTHCHECK_REPAIR_AGENT_UID" "$NODE_AGENT_HOME" "$check_name"
}
