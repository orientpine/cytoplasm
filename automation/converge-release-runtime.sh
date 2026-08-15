#!/usr/bin/env bash
# automation/converge-release-runtime.sh — install a pinned origin/main commit as
# a release and flip /srv/autophagy-agent-current to it (DG-4).
#
# Run as ops by deploy-skill.sh (when the release runtime already exists) and by
# land.sh, so peer attestation, the mount and the landing all run from an
# immutable, read-only release rather than the mutable resident mirror. Because
# the release tree is built from a SHA-pinned origin/main snapshot, a parallel
# session's dirty/ahead mirror can no longer block or contaminate the deploy.
#
# Env:
#   RELEASE_EXPECTED_SHA      converge to THIS commit; default = origin/main now.
#                             A caller that already pushed must pin its own sha —
#                             re-reading origin here would install whatever landed
#                             most recently, i.e. somebody else's commit (DG-6).
#   RELEASE_MIRROR_CHECKOUT   default /srv/autophagy-agents (the git object source)
#   RELEASE_INSTALL_HELPER    default /usr/local/libexec/autophagy-install-release
#   RELEASE_STORE_PARENT      default /srv (PARENT of autophagy-agent-releases/ +
#                             autophagy-agent-current — the canonical runtime paths)
#   RELEASE_CONVERGE_LOCK     default /tmp/autophagy-release-converge.lock (fixed:
#                             a shared lock must be the same file for every caller)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
eval "$(python3 "$REPO_ROOT/automation/node_config_sh.py" --print-env)"
MIRROR="${RELEASE_MIRROR_CHECKOUT:-$NODE_DEPLOY_CHECKOUT}"
HELPER="${RELEASE_INSTALL_HELPER:-/usr/local/libexec/autophagy-install-release}"
STORE_PARENT="${RELEASE_STORE_PARENT:-$NODE_SERVICE_ROOT}"
# A FIXED default path: this lock only works by being the same file for every
# caller, and $TMPDIR differs between shells (origin_snapshot's per-run lock can
# afford to follow it; a shared one cannot).
#
# NOT in /tmp. The other holder of this lock is root (the reconcile timer's helper),
# and `fs.protected_regular=2` refuses an open in a sticky, world-writable directory
# when the file's owner differs from the opener. Measured 2026-08-01: ops had created
# the /tmp lock first, so root could never open it and converged nothing for hours.
LOCK_DIR="${RELEASE_CONVERGE_LOCK_DIR:-$NODE_PRIVATE_ROOT/locks}"
LOCK="${RELEASE_CONVERGE_LOCK:-$LOCK_DIR/release-converge.lock}"
LOCK_GROUP=autophagy

log() { printf '[converge-release] %s\n' "$*" >&2; }

# The snapshot primitive comes from THIS tree — the immutable release when we are
# run from one. Sourcing it out of $MIRROR would execute a parallel session's
# uncommitted shell as ops, which is exactly what pinning the snapshot avoids.
# shellcheck source=automation/origin_snapshot.sh
source "$(dirname "${BASH_SOURCE[0]}")/origin_snapshot.sh"

expected_sha="${RELEASE_EXPECTED_SHA:-}"
if [[ -z "$expected_sha" ]]; then
  expected_sha="$(git -C "$MIRROR" ls-remote origin refs/heads/main | awk '{print $1}')"
fi
[[ "$expected_sha" =~ ^[0-9a-f]{40,64}$ ]] \
  || { log "SYNC-BLOCK: cannot resolve an origin/main sha"; exit 4; }

# `current` is flipped by both deploy-skill.sh and land.sh. origin_snapshot's own
# lock is released before the command runs, so two overlapping convergences could
# otherwise finish out of order and flip the runtime BACKWARDS onto an older sha.
# Best effort as ops: root's provisioner owns the directory, this only covers a node
# where it has not run yet. setgid + 007 keep the file openable by BOTH accounts.
if [[ ! -d "$LOCK_DIR" ]]; then
  mkdir -p "$LOCK_DIR" 2>/dev/null \
    && chgrp "$LOCK_GROUP" "$LOCK_DIR" 2>/dev/null \
    && chmod 2770 "$LOCK_DIR" 2>/dev/null || true
fi
[[ -e "$LOCK" ]] || (umask 007; : > "$LOCK") 2>/dev/null || true
exec 9>"$LOCK"
flock -x -w 600 9 \
  || { log "SYNC-BLOCK: another release convergence is still running"; exit 5; }

# Inside the pinned snapshot: tar the verified tree by value into the release store
# helper, then verify current points at exactly this sha. The snapshot primitive
# fetches origin, pins expected_sha, and fails closed if origin moved meanwhile.
# The install MUST run under `sudo -n`: ops executes a root-owned helper but does
# not thereby gain root, and installing under /srv needs it (sudoers grants ops
# `install --sha *`). `current --verify` is read-only, so it needs no sudo.
origin_snapshot_run "$MIRROR" "$expected_sha" bash -c '
  set -euo pipefail
  tar -C "$AUTOPHAGY_SNAPSHOT_DIR" --exclude=.git -czf - . \
    | sudo -n "'"$HELPER"'" install --sha "'"$expected_sha"'" --store-root "'"$STORE_PARENT"'" --git-root "'"$MIRROR"'"
  "'"$HELPER"'" current --verify "'"$expected_sha"'" --store-root "'"$STORE_PARENT"'"
'
log "converged: current -> $expected_sha"
