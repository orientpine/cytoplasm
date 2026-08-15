#!/usr/bin/env bash

report_roster_identity() {
  local response remote_command

  printf -v remote_command \
    'sudo -n -u %s -H env PYTHONPATH=%q /usr/bin/python3 -m automation.group_roster identity %q' \
    "$NODE_AGENT_ACCOUNT" "$NODE_RELEASE_CURRENT" "$NODE_AGENT_HOME/.hermes/roster.yaml"
  if response="$(capture_on_node "$PRIMARY_NODE" "$remote_command")" \
      && [[ "$response" == ROSTER-IDENTITY\ * && "$response" != *$'\n'* ]]; then
    log "INFO ${response}"
    return 0
  fi
  log "INFO ROSTER-UNAVAILABLE"
  return 0
}
