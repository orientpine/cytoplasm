#!/usr/bin/env bash
# automation/provision-deploy-converge.sh — install the root-owned convergence helper
# and the root-owned copies of its dependencies.
#
# WHY the dependencies are copied rather than sourced (MD-1): the helper runs under
# sudo. If it sourced origin_snapshot.sh / release_store.py out of the deploy checkout
# or the current release, every merge would rewrite privileged code — "merge a PR"
# would become "run arbitrary code as root". Copying them here makes updating the
# privileged path a deliberate, root-only act, exactly like provision-release-store.sh
# does for the install helper.
#
# Idempotent: `install` replaces each single path, so re-running converges rather than
# accumulating. Run it again after changing any installed source.
#
# Env (test seams; default to the real node paths):
#   HELPER_PATH                  default /usr/local/libexec/autophagy-converge-origin-main
#   HELPER_LIBDIR                default /usr/local/libexec/autophagy-converge.d
#   DEPLOY_CONVERGE_ASSUME_ROOT  override the root check for hermetic tests
set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly HELPER_PATH="${HELPER_PATH:-/usr/local/libexec/autophagy-converge-origin-main}"
readonly HELPER_LIBDIR="${HELPER_LIBDIR:-/usr/local/libexec/autophagy-converge.d}"
readonly RENDER_DIR="$(mktemp -d)"
trap 'rm -rf -- "$RENDER_DIR"' EXIT

log() { printf '[provision-deploy-converge] %s\n' "$*"; }
die() { log "ERROR: $1" >&2; exit 1; }

is_root() {
  if [[ -n "${DEPLOY_CONVERGE_ASSUME_ROOT:-}" ]]; then
    [[ "$DEPLOY_CONVERGE_ASSUME_ROOT" == "1" ]]
  else
    [[ "$EUID" == 0 ]]
  fi
}
is_root || die "run as root: sudo bash automation/provision-deploy-converge.sh"

command -v install >/dev/null || die "required command missing: install"

readonly HELPER_SRC="$REPO_ROOT/automation/converge_origin_main.sh"
readonly SNAPSHOT_SRC="$REPO_ROOT/automation/origin_snapshot.sh"
readonly STORE_SRC="$REPO_ROOT/automation/release_store.py"
readonly PROVENANCE_SRC="$REPO_ROOT/automation/release_provenance.py"
readonly PACKAGE_INIT_SRC="$REPO_ROOT/automation/__init__.py"
readonly SIGNATURE_SRC="$REPO_ROOT/automation/git_tag_signature.py"
readonly UPDATE_TRUST_SRC="$REPO_ROOT/automation/update_trust.py"
readonly UPDATE_TRUST_STATE_SRC="$REPO_ROOT/automation/update_trust_state.py"
readonly NODE_CONFIG_SRC="$REPO_ROOT/automation/node_config.py"
readonly NODE_SEED_SRC="$REPO_ROOT/configs/node.example.toml"
for src in \
  "$HELPER_SRC" "$SNAPSHOT_SRC" "$STORE_SRC" "$PROVENANCE_SRC" \
  "$PACKAGE_INIT_SRC" "$SIGNATURE_SRC" "$UPDATE_TRUST_SRC" "$UPDATE_TRUST_STATE_SRC" \
  "$NODE_CONFIG_SRC" "$NODE_SEED_SRC"; do
  [[ -f "$src" ]] || die "tracked source missing: $src"
done
python3 "$REPO_ROOT/automation/node_asset_renderer.py" "$HELPER_SRC" "$RENDER_DIR/helper"

install -d -m 0755 -o root -g root \
  "$(dirname "$HELPER_PATH")" "$HELPER_LIBDIR" "$HELPER_LIBDIR/automation"
install -m 0755 -o root -g root "$RENDER_DIR/helper" "$HELPER_PATH"
install -m 0755 -o root -g root "$SNAPSHOT_SRC" "$HELPER_LIBDIR/origin_snapshot.sh"
install -m 0755 -o root -g root "$STORE_SRC" "$HELPER_LIBDIR/release_store.py"
install -m 0644 -o root -g root "$PROVENANCE_SRC" "$HELPER_LIBDIR/release_provenance.py"
install -m 0644 -o root -g root "$PACKAGE_INIT_SRC" "$HELPER_LIBDIR/automation/__init__.py"
install -m 0644 -o root -g root "$SIGNATURE_SRC" "$HELPER_LIBDIR/automation/git_tag_signature.py"
install -m 0755 -o root -g root "$UPDATE_TRUST_SRC" "$HELPER_LIBDIR/automation/update_trust.py"
# C1 anti-rollback floor. The verifier imports it, so a libdir without it turns
# every convergence into an ImportError under sudo rather than a refusal.
install -m 0644 -o root -g root "$UPDATE_TRUST_STATE_SRC" "$HELPER_LIBDIR/automation/update_trust_state.py"
install -m 0644 -o root -g root "$NODE_CONFIG_SRC" "$HELPER_LIBDIR/automation/node_config.py"
install -m 0644 -o root -g root "$NODE_SEED_SRC" "$HELPER_LIBDIR/automation/node.example.toml"

# The shared convergence lock lives here rather than /tmp: root (this helper) and ops
# (the deploy-side converger) must open the SAME file, and fs.protected_regular=2
# refuses a cross-owner open in a sticky, world-writable directory. setgid so the
# file's group is the same whoever creates it first.
readonly LOCK_DIR="${LOCK_DIR:-/srv/autophagy-private/locks}"
install -d -m 2770 -o ops -g autophagy "$LOCK_DIR"

log "READY helper=$HELPER_PATH libdir=$HELPER_LIBDIR locks=$LOCK_DIR"
