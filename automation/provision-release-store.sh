#!/usr/bin/env bash
# automation/provision-release-store.sh — install root-only release and paired-gateway
# helpers, then create the immutable release store root.
#
# WHY it is its OWN file (2026-07-31): the release/runtime-root work must not touch
# automation/bootstrap-accounts.sh, which the active repair-report-rollout plan
# claims as its sole source change (collision C2). This provisioner mirrors
# provision-skill-roots.sh instead: create /srv/autophagy-agent-releases
# (0755 root:root), install automation/release_store.py as the privileged helper,
# and install the sudoers stanza that lets ops call it. Idempotent.
#
# Env (test seams; default to the real node paths):
#   RELEASE_STORE_ROOT             default /srv/autophagy-agent-releases
#   HELPER_PATH                    default /usr/local/libexec/autophagy-install-release
#   SUDOERS_PATH                   default /etc/sudoers.d/autophagy-release-store
#   RELEASE_PROVISION_ASSUME_ROOT  override the root check for hermetic tests
set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
eval "$(python3 "$REPO_ROOT/automation/node_config_sh.py" --print-env)"
readonly RELEASE_STORE_ROOT="${RELEASE_STORE_ROOT:-$NODE_RELEASE_STORE}"
readonly HELPER_PATH="${HELPER_PATH:-$NODE_LIBEXEC_DIR/autophagy-install-release}"
readonly GATEWAY_HELPER_PATH="${GATEWAY_HELPER_PATH:-$NODE_LIBEXEC_DIR/autophagy-gateway-pair}"
readonly PROVENANCE_PATH="${PROVENANCE_PATH:-$(dirname "$HELPER_PATH")/release_provenance.py}"
readonly SUDOERS_PATH="${SUDOERS_PATH:-/etc/sudoers.d/autophagy-release-store}"
readonly RENDER_DIR="$(mktemp -d)"
trap 'rm -rf -- "$RENDER_DIR"' EXIT

log() { printf '[provision-release-store] %s\n' "$*"; }
die() { log "ERROR: $1" >&2; exit 1; }

is_root() {
  if [[ -n "${RELEASE_PROVISION_ASSUME_ROOT:-}" ]]; then
    [[ "$RELEASE_PROVISION_ASSUME_ROOT" == "1" ]]
  else
    [[ "$EUID" == 0 ]]
  fi
}
is_root || die "run as root: sudo bash automation/provision-release-store.sh"

for command_name in install python3 visudo; do
  command -v "$command_name" >/dev/null || die "required command missing: $command_name"
done
[[ -f "$REPO_ROOT/automation/release_store.py" ]] || die "release_store.py missing"
[[ -f "$REPO_ROOT/automation/release_provenance.py" ]] || die "release_provenance.py missing"
[[ -f "$REPO_ROOT/automation/gateway_pair.py" ]] || die "gateway_pair.py missing"

# Store root and helper install dir (idempotent: install -d is a no-op if present).
install -d -m 0755 -o root -g root "$(dirname "$HELPER_PATH")" "$RELEASE_STORE_ROOT"

# Install the privileged helper by value; re-running replaces the single path.
install -m 0755 -o root -g root "$REPO_ROOT/automation/release_store.py" "$HELPER_PATH"
python3 "$REPO_ROOT/automation/node_asset_renderer.py" \
  "$REPO_ROOT/automation/gateway_pair.py" "$RENDER_DIR/autophagy-gateway-pair"
install -m 0755 -o root -g root "$RENDER_DIR/autophagy-gateway-pair" "$GATEWAY_HELPER_PATH"
install -d -m 0755 -o root -g root "$(dirname "$HELPER_PATH")/automation" "$(dirname "$HELPER_PATH")/configs"
install -m 0644 -o root -g root "$REPO_ROOT/automation/node_config.py" "$(dirname "$HELPER_PATH")/automation/node_config.py"
install -m 0644 -o root -g root "$REPO_ROOT/configs/node.example.toml" "$(dirname "$HELPER_PATH")/configs/node.example.toml"
install -m 0644 -o root -g root "$REPO_ROOT/automation/release_provenance.py" "$PROVENANCE_PATH"

# Sudoers stanza: install the tracked source (idempotent; install -m 0440 replaces
# the single path each run). The stanza references the fixed HELPER install path.
sudoers_src="$REPO_ROOT/automation/sudoers.d/autophagy-release-store"
[[ -f "$sudoers_src" ]] || die "tracked sudoers source missing: $sudoers_src"
install -d -m 0755 -o root -g root "$(dirname "$SUDOERS_PATH")"
python3 "$REPO_ROOT/automation/node_asset_renderer.py" "$sudoers_src" "$SUDOERS_PATH.tmp"
install -m 0440 -o root -g root "$SUDOERS_PATH.tmp" "$SUDOERS_PATH"
rm -f "$SUDOERS_PATH.tmp"
visudo -cf "$SUDOERS_PATH" >/dev/null

log "READY store_root=$RELEASE_STORE_ROOT helper=$HELPER_PATH gateway_pair=$GATEWAY_HELPER_PATH sudoers=$SUDOERS_PATH"
