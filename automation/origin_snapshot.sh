#!/usr/bin/env bash
# automation/origin_snapshot.sh — run a command against a SHA-pinned origin/main
# snapshot, never against the resident deploy checkout's mutable working tree.
#
# WHY (2026-07-31): a parallel session's uncommitted edits in /srv/autophagy-agents
# block `git pull --ff-only`, so already-merged code cannot deploy. This primitive
# fetches origin, pins an EXPECTED sha, materializes exactly that commit in an
# ephemeral `git worktree add --detach` tree, runs a command inside it, and cleans
# up — preserving the command's exit code. A dirty OR ahead resident checkout can
# no longer block or contaminate a deploy: the worktree is built from the object
# store at the pinned sha, independent of the resident HEAD or index.
#
# Usage (sourced):
#   source automation/origin_snapshot.sh
#   origin_snapshot_run <mirror_checkout> <expected_sha> <command...>
#
# The command runs with cwd = the snapshot tree and AUTOPHAGY_SNAPSHOT_DIR set.
# Only the short git-metadata prep and cleanup windows are flock-serialized; the
# command itself runs outside the lock so concurrent deploys are not serialized.

origin_snapshot_log() { printf '[origin-snapshot] %s\n' "$*" >&2; }

_origin_snapshot_prune_stale() { # <mirror_checkout>
  local mirror="$1" lock="${TMPDIR:-/tmp}/autophagy-origin-snapshot.lock"
  (
    flock -x 9
    local listing candidate snapshot line registered status head_sha
    listing="$(git -C "$mirror" worktree list --porcelain 2>/dev/null)" || return 0
    while IFS= read -r -d '' candidate; do
      snapshot="$candidate/tree"
      registered=0
      while IFS= read -r line; do
        if [[ "$line" == "worktree $snapshot" ]]; then
          registered=1
          break
        fi
      done <<<"$listing"
      if (( registered == 0 )); then
        rm -rf -- "$candidate"
        continue
      fi
      status="$(git -C "$snapshot" status --porcelain 2>/dev/null)" || continue
      [[ -z "$status" ]] || continue
      head_sha="$(git -C "$snapshot" rev-parse --verify --quiet 'HEAD^{commit}' 2>/dev/null)" \
        || continue
      git -C "$mirror" merge-base --is-ancestor "$head_sha" refs/remotes/origin/main \
        || continue
      git -C "$mirror" worktree remove --force -- "$snapshot" || continue
      rm -rf -- "$candidate"
    done < <(
      # Six hours is longer than a normal deploy but bounds abandoned snapshots.
      find "${TMPDIR:-/tmp}" -mindepth 1 -maxdepth 1 -type d \
        -name 'autophagy-snapshot.*' -mmin +360 -print0
    )
  ) 9>"$lock"
}

origin_snapshot_run() ( # <mirror_checkout> <expected_sha> <command...>
  local mirror="$1" expected_sha="$2"
  shift 2 || { origin_snapshot_log "SNAPSHOT-BLOCK: usage: <mirror> <sha> <command...>"; return 2; }
  [[ $# -gt 0 ]] || { origin_snapshot_log "SNAPSHOT-BLOCK: no command given"; return 2; }
  [[ "$expected_sha" =~ ^[0-9a-f]{40,64}$ ]] \
    || { origin_snapshot_log "SNAPSHOT-BLOCK: expected sha is not a hex commit id"; return 2; }
  git -C "$mirror" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || { origin_snapshot_log "SNAPSHOT-BLOCK: $mirror is not a git checkout"; return 2; }

  local lock="${TMPDIR:-/tmp}/autophagy-origin-snapshot.lock"
  local parent snapshot rc=0
  _origin_snapshot_prune_stale "$mirror"
  parent="$(mktemp -d "${TMPDIR:-/tmp}/autophagy-snapshot.XXXXXXXX")" \
    || { origin_snapshot_log "SNAPSHOT-BLOCK: cannot create snapshot workspace"; return 2; }
  snapshot="$parent/tree"

  # Cleanup restores the command's rc; it must never mask a real failure.
  _origin_snapshot_cleanup() {
    (
      flock -x 9
      git -C "$mirror" worktree remove --force -- "$snapshot" 2>/dev/null \
        || rm -rf -- "$snapshot"
      git -C "$mirror" worktree prune 2>/dev/null || true
    ) 9>"$lock"
    rm -rf -- "$parent"
  }
  # Function-subshell traps cannot escape into the sourced-library caller.
  trap '_origin_snapshot_cleanup' EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM

  # --- prep window (locked): fetch, verify pinned sha, materialize worktree ---
  {
    flock -x 9
    git -C "$mirror" fetch --quiet origin '+refs/heads/main:refs/remotes/origin/main' \
      || { origin_snapshot_log "SNAPSHOT-BLOCK: fetch origin failed"; rc=2; }
    if (( rc == 0 )); then
      local remote_sha
      remote_sha="$(git -C "$mirror" rev-parse --verify --quiet 'refs/remotes/origin/main^{commit}' 2>/dev/null)"
      if [[ "$remote_sha" != "$expected_sha" ]]; then
        origin_snapshot_log "SNAPSHOT-BLOCK: remote main moved (want $expected_sha, got ${remote_sha:-<none>})"
        rc=3
      fi
    fi
    if (( rc == 0 )); then
      git -C "$mirror" worktree add --detach --quiet "$snapshot" "$expected_sha" \
        || { origin_snapshot_log "SNAPSHOT-BLOCK: worktree add failed"; rc=2; }
    fi
  } 9>"$lock"
  if (( rc != 0 )); then return "$rc"; fi

  # --- verify the materialized tree is exactly the pinned, clean commit -------
  local head_sha
  head_sha="$(git -C "$snapshot" rev-parse HEAD 2>/dev/null)"
  if [[ "$head_sha" != "$expected_sha" ]]; then
    origin_snapshot_log "SNAPSHOT-BLOCK: snapshot HEAD $head_sha != $expected_sha"
    return 2
  fi
  if [[ -n "$(git -C "$snapshot" status --porcelain --untracked-files=no 2>/dev/null)" ]]; then
    origin_snapshot_log "SNAPSHOT-BLOCK: snapshot tree is unexpectedly dirty"
    return 2
  fi

  # --- run the command outside the lock, preserving its exit code -------------
  ( cd "$snapshot" && AUTOPHAGY_SNAPSHOT_DIR="$snapshot" "$@" )
  rc=$?
  return "$rc"
)
