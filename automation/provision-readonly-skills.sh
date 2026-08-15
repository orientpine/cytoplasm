#!/usr/bin/env bash
set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
eval "$(python3 "$REPO_ROOT/automation/node_config_sh.py" --print-env)"
readonly STORE_ROOT="$NODE_SKILL_STORE"
readonly LIVE_ROOT="$STORE_ROOT/live"
readonly TARGET="$NODE_AGENT_HOME/.hermes/skills"
readonly HUB_STATE="$NODE_AGENT_HOME/.hermes/skill-hub-state"
readonly HUB_TARGET="$TARGET/.hub"
readonly HELPER="$NODE_LIBEXEC_DIR/autophagy-install-skill"
readonly SUDOERS="/etc/sudoers.d/autophagy-skill-store"
readonly FSTAB_ENTRY="$LIVE_ROOT $TARGET none bind,ro,nosuid,nodev 0 0"
readonly HUB_FSTAB_ENTRY="$HUB_STATE $HUB_TARGET none bind,rw,nosuid,nodev,noexec 0 0"

log() { printf '[provision-readonly-skills] %s\n' "$*"; }
die() { log "ERROR: $1" >&2; exit 1; }

[[ "$EUID" == 0 ]] || die "run as root: sudo bash automation/provision-readonly-skills.sh"
for command_name in install python3 tar mount mountpoint findmnt visudo systemctl; do
  command -v "$command_name" >/dev/null || die "required command missing: $command_name"
done
id "$NODE_AGENT_ACCOUNT" >/dev/null 2>&1 || die "agent account missing"
[[ -d "$REPO_ROOT/skills" ]] || die "canonical skills directory missing: $REPO_ROOT/skills"

install -d -m 0755 -o root -g root /usr/local/libexec "$STORE_ROOT" "$STORE_ROOT/releases" "$LIVE_ROOT"
install -d -m 0755 -o root -g root "$LIVE_ROOT/.hub"
install -d -m 0700 -o "$NODE_AGENT_ACCOUNT" -g "$NODE_AGENT_ACCOUNT" "$HUB_STATE"
install -m 0755 -o root -g root "$REPO_ROOT/automation/skill_store.py" "$HELPER"
python3 "$REPO_ROOT/automation/node_asset_renderer.py" "$REPO_ROOT/automation/sudoers.d/autophagy-skill-store" "$SUDOERS.tmp"
install -m 0440 -o root -g root "$SUDOERS.tmp" "$SUDOERS"
rm -f "$SUDOERS.tmp"
visudo -cf "$SUDOERS" >/dev/null

for source in "$REPO_ROOT"/skills/*; do
  [[ -f "$source/SKILL.md" ]] || continue
  skill="$(basename "$source")"
  digest="$(PYTHONPATH="$REPO_ROOT" python3 -c 'from pathlib import Path; from automation.skill_review import skill_digest; import sys; print(skill_digest(Path(sys.argv[1])))' "$source")"
  tar -C "$(dirname "$source")" --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' -czf - "$skill" \
    | "$HELPER" install --skill "$skill" --hash "$digest"
done

if ! mountpoint -q "$TARGET"; then
  if [[ -d "$TARGET" ]]; then
    backup="${TARGET}.agent-owned.$(date -u +%Y%m%dT%H%M%SZ)"
    mv "$TARGET" "$backup"
    log "agent-owned skill directory backed up: $backup"
  fi
  install -d -m 0755 -o root -g root "$TARGET"
  mount --bind "$LIVE_ROOT" "$TARGET"
  mount -o remount,bind,ro,nosuid,nodev "$TARGET"
fi

if ! mountpoint -q "$HUB_TARGET"; then
  mount --bind "$HUB_STATE" "$HUB_TARGET"
  mount -o remount,bind,rw,nosuid,nodev,noexec "$HUB_TARGET"
fi

if ! grep -Fqx "$FSTAB_ENTRY" /etc/fstab; then
  printf '%s\n' "$FSTAB_ENTRY" >> /etc/fstab
fi
if ! grep -Fqx "$HUB_FSTAB_ENTRY" /etc/fstab; then
  printf '%s\n' "$HUB_FSTAB_ENTRY" >> /etc/fstab
fi
systemctl daemon-reload

options="$(findmnt -no OPTIONS "$TARGET")"
hub_options="$(findmnt -no OPTIONS "$HUB_TARGET")"
[[ ",$options," == *,ro,* ]] || die "target mount is not read-only: $options"
[[ "$(findmnt -no FSROOT "$TARGET")" == "$LIVE_ROOT" ]] || die "target mount source mismatch"
[[ ",$hub_options," == *,rw,* && ",$hub_options," == *,noexec,* ]] || die "hub state mount options invalid: $hub_options"
[[ "$(findmnt -no FSROOT "$HUB_TARGET")" == "$HUB_STATE" ]] || die "hub state mount source mismatch"
sudo -n -u "$NODE_AGENT_ACCOUNT" -H sh -lc 'export PATH="$HOME/.local/bin:$PATH"; hermes skills list >/dev/null'
log "READY source=$LIVE_ROOT target=$TARGET options=$options hub_options=$hub_options"
