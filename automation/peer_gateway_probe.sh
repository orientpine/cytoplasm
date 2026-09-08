#!/usr/bin/env bash
# Read-only config drift detection, not a claim about the running gateway process.
set -euo pipefail
if [[ -z "${NODE_PEER_HOME:-}" || -z "${NODE_PEER_ACCOUNT:-}" || -z "${NODE_OPS_ACCOUNT:-}" ]]; then
  eval "$(python3 "$(dirname "${BASH_SOURCE[0]}")/node_config_sh.py" --print-env)"
fi
readonly PEER_GATEWAY_CONFIG="${HEALTHCHECK_PEER_GATEWAY_CONFIG:-$NODE_PEER_HOME/.hermes/config.yaml}"
readonly PEER_CHANNEL_DIRECTORY="${HEALTHCHECK_PEER_CHANNEL_DIRECTORY:-$NODE_PEER_HOME/.hermes/channel_directory.json}"

peer_ignored_channels_guidance() {
  printf '%s\n' \
    'PEER-IGNORED-CHANNELS-RECOVERY: OWNER restore top-level discord.ignored_channels from the unique approvals entry in the peer channel directory; then follow operations.md gateway-pair restart rules. Never auto-edit config or restart.' \
    'PEER-IGNORED-CHANNELS-RECOVERY: if unreadable, OWNER review/install these exact read-only sudoers entries with visudo (no shell or Python sudo grant):'
  printf '%s ALL=(%s) NOPASSWD: /usr/bin/cat -- %s\n' "$NODE_OPS_ACCOUNT" "$NODE_PEER_ACCOUNT" "$PEER_GATEWAY_CONFIG" "$NODE_OPS_ACCOUNT" "$NODE_PEER_ACCOUNT" "$PEER_CHANNEL_DIRECTORY"
  printf '%s\n' 'PEER-IGNORED-CHANNELS-RECOVERY: parser unavailable: OWNER provide python3 with PyYAML (Debian/Ubuntu: apt-get install python3-yaml).'
}

peer_gateway_read() {
  # Only these two fixed paths are passed by the probe; data never reaches argv/logs.
  if [[ -r "$1" ]]; then
    /usr/bin/cat -- "$1" 2>/dev/null
  else
    sudo -n -u "$NODE_PEER_ACCOUNT" /usr/bin/cat -- "$1" 2>/dev/null
  fi
}

probe_peer_ignored_channels() {
  local config directory
  if ! config="$(peer_gateway_read "$PEER_GATEWAY_CONFIG")" \
    || ! directory="$(peer_gateway_read "$PEER_CHANNEL_DIRECTORY")"; then
    printf '%s\n' '[healthcheck] PEER-IGNORED-CHANNELS-UNREADABLE'
    peer_ignored_channels_guidance
    return 1
  fi
  # A full YAML parser rejects malformed surrounding config as well as this field.
  # PyYAML is already present in the node's ops python3 and peer Hermes venv.
  # Keep config (which can contain secrets) on inherited fds, never argv or env.
  if python3 - 4<<< "$config" 5<<< "$directory" <<'PY'
from __future__ import annotations

import json
import os
from typing import NewType

try:
    import yaml
except ImportError:
    print("[healthcheck] PEER-IGNORED-CHANNELS-PARSER-UNAVAILABLE")
    raise SystemExit(1) from None

ChannelId = NewType("ChannelId", str)


class ProbeInputError(Exception):
    """An input cannot establish the required exclusion; contents stay private."""


def field(node: yaml.Node, name: str) -> yaml.Node:
    """Parse a unique mapping field, rejecting duplicate or missing keys."""
    if not isinstance(node, yaml.MappingNode):
        raise ProbeInputError
    matches = [value for key, value in node.value
               if isinstance(key, yaml.ScalarNode) and key.value == name]
    if len(matches) != 1:
        raise ProbeInputError
    return matches[0]


try:
    with os.fdopen(5, encoding="utf-8") as stream:
        raw = json.load(stream)
    if not isinstance(raw, dict) or not isinstance(raw.get("platforms"), dict):
        raise ProbeInputError
    channels = raw["platforms"].get("discord")
    if not isinstance(channels, list) or not all(isinstance(row, dict) for row in channels):
        raise ProbeInputError
    matches = [row.get("id") for row in channels if row.get("name") == "approvals"]
    if len(matches) != 1 or not isinstance(matches[0], str) or not matches[0].isascii() or not matches[0].isdigit():
        raise ProbeInputError
    approvals = ChannelId(matches[0])
    with os.fdopen(4, encoding="utf-8") as stream:
        root = yaml.compose(stream, Loader=yaml.SafeLoader)
    ignored = field(field(root, "discord"), "ignored_channels")
    # Parse untrusted YAML node kinds here; internal membership uses ChannelId only.
    if isinstance(ignored, yaml.SequenceNode):
        entries = ignored.value
        if not all(isinstance(entry, yaml.ScalarNode) and entry.tag in (
            "tag:yaml.org,2002:str", "tag:yaml.org,2002:int",
        ) for entry in entries):
            raise ProbeInputError
        # Hermes does not split CSV inside a list item.
        ids = {ChannelId(entry.value.strip()) for entry in entries}
    elif isinstance(ignored, yaml.ScalarNode) and ignored.tag == "tag:yaml.org,2002:str":
        ids = {ChannelId(part.strip()) for part in ignored.value.split(",")}
    else:
        raise ProbeInputError
    if approvals not in ids:
        print("[healthcheck] PEER-IGNORED-CHANNELS-MISSING")
        raise SystemExit(1)
except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError, ProbeInputError):
    print("[healthcheck] PEER-IGNORED-CHANNELS-INVALID")
    raise SystemExit(1) from None
print("[healthcheck] PEER-IGNORED-CHANNELS-PASS")
PY
  then
    return 0
  fi
  peer_ignored_channels_guidance
  return 1
}
