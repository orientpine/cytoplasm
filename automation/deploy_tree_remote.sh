#!/usr/bin/env bash
# automation/deploy_tree_remote.sh — the node half of automation/deploy_tree.sh: every function
# here is shipped to the node by `declare -f` in deploy_tree_swap's prelude and runs there in
# a fresh shell (no errexit). Sourced by deploy_tree.sh only; callers source deploy_tree.sh.
# Remote rc: 0 done · 5 refused, active path unchanged or restored · 6 lock busy ·
#   7 state unknown (the step could not be undone; this run's tree may be active).

_deploy_tree_block() { printf 'TREE-DEPLOY-BLOCK: %s\n' "$*" >&2; }

# Snapshot format: <sha256>|<relative path>, ordered by path bytes. Runs remotely too.
# Cache directories are skipped on both sides (the standing probe's set): a live importer
# writes __pycache__ into the tree it just loaded, which must not fail the verify step.
_deploy_tree_snapshot() (
  set -o pipefail
  cd -- "$1" || exit 1
  find . -type d \( -name __pycache__ -o -name .venv -o -name .ruff_cache -o -name .pytest_cache \) -prune \
    -o -type f -printf '%P\0' | LC_ALL=C sort -z | while IFS= read -r -d '' file; do
    hash="$(sha256sum < "$file")" || exit 1
    printf '%s|%s\n' "${hash%% *}" "$file"
  done
)

# One renameat2 call: `noreplace` never overwrites, `exchange` swaps two existing paths.
_deploy_tree_rename() { # noreplace|exchange <from> <to>
  python3 - "$@" <<'PY'
import ctypes, os, sys
flags = {"noreplace": 1, "exchange": 2}[sys.argv[1]]
libc = ctypes.CDLL(None, use_errno=True)
if libc.renameat2(-100, os.fsencode(sys.argv[2]), -100, os.fsencode(sys.argv[3]), flags):
    sys.exit("renameat2 failed: " + os.strerror(ctypes.get_errno()))
PY
}

# Append-open (never truncate) and hold an exclusive lock on fd 9 until the shell exits.
_deploy_tree_lock() { # <home-rel-lock or empty> <wait-seconds>
  [[ -z "$1" ]] && return 0
  mkdir -p -- "$(dirname -- "$1")" && exec 9>>"$1" && flock -w "$2" 9
}

_deploy_tree_remote_prepare() {
  if [[ "$link" == 1 && -e "$dest" && ! -L "$dest" ]]; then
    _deploy_tree_block "$HOME/$dest is not a generation link"; return 5
  fi
  if [[ "$link" != 1 && ( -L "$dest" || ( -e "$dest" && ! -d "$dest" ) ) ]]; then
    _deploy_tree_block "$HOME/$dest is not a real directory"; return 5
  fi
  if ! { mkdir -p -- "$(dirname -- "$staging")" && mkdir -- "$staging"; }; then
    _deploy_tree_block "cannot create staging $HOME/$staging"; return 5
  fi
  mkdir -p -- "$staging/$prefix" && tar -xzf - -C "$staging/$prefix" \
    && find "$staging" -type d -exec chmod 700 {} + \
    && find "$staging" -type f -exec chmod 600 {} + \
    && stat -c 'id=%d:%i' -- "$staging" && _deploy_tree_snapshot "$staging" || return 3
}

_deploy_tree_switch_directory() {
  if [[ ! -e "$dest" && ! -L "$dest" ]]; then
    _deploy_tree_rename noreplace "$staging" "$dest" && return 0
  elif [[ -d "$dest" && ! -L "$dest" ]]; then
    if [[ -e "$backup" || -L "$backup" ]]; then
      rm -rf -- "$staging"; _deploy_tree_block "backup name taken: $HOME/$backup"; return 5
    fi
    if _deploy_tree_rename exchange "$staging" "$dest"; then
      _deploy_tree_rename noreplace "$staging" "$backup" && return 0
      # The previous tree now sits at the staging name: swap it back, drop this run's tree.
      if _deploy_tree_rename exchange "$staging" "$dest" && _deploy_tree_discard_ours; then
        _deploy_tree_block "could not keep the previous tree as $HOME/$backup"; return 5
      fi
      _deploy_tree_block "could not keep the previous tree as $HOME/$backup nor swap back: state unknown"; return 7
    fi
  fi
  # A renameat2 that swapped but reported failure leaves the PREVIOUS tree at the staging name.
  _deploy_tree_discard_ours || {
    _deploy_tree_block "cannot switch $HOME/$dest atomically; $HOME/$staging is not this run's tree, kept: state unknown"; return 7; }
  _deploy_tree_block "cannot switch $HOME/$dest atomically"; return 5
}

_deploy_tree_switch_link() { # <expected snapshot>
  local previous="" actual
  if [[ -e "$dest" && ! -L "$dest" ]]; then
    rm -rf -- "$staging"; _deploy_tree_block "$HOME/$dest is not a generation link"; return 5
  fi
  if [[ -e "$gen" || -L "$gen" ]] || ! mv -T -- "$staging" "$gen"; then
    rm -rf -- "$staging"; _deploy_tree_block "cannot place generation $HOME/$gen"; return 5
  fi
  # Verify before publishing: a mismatch here was never visible, so it can be removed.
  if ! actual="$(_deploy_tree_snapshot "$gen")" || [[ "$actual" != "$1" ]]; then
    rm -rf -- "$gen"
    _deploy_tree_block "pre-publish verification of $HOME/$gen failed; link unchanged"; return 5
  fi
  # Without the previous target a later rollback would remove the link instead of restoring it.
  if [[ -L "$dest" ]] && ! previous="$(readlink -- "$dest")"; then
    rm -rf -- "$gen"; _deploy_tree_block "cannot read link $HOME/$dest; link unchanged"; return 5
  fi
  if ! ln -s -- "$target" "$link_tmp"; then
    rm -rf -- "$gen"; _deploy_tree_block "cannot create $HOME/$link_tmp"; return 5
  fi
  if ! mv -T -- "$link_tmp" "$dest"; then
    rm -f -- "$link_tmp"; rm -rf -- "$gen"
    _deploy_tree_block "cannot switch $HOME/$dest atomically"; return 5
  fi
  printf 'previous=%s\n' "$previous"
}

_deploy_tree_remote_switch() {
  local expected
  expected="$(cat)"
  if ! _deploy_tree_lock "$lock" "$wait"; then
    rm -rf -- "$staging"
    _deploy_tree_block "lock busy: $HOME/$lock (waited ${wait}s); nothing switched"; return 6
  fi
  if [[ ! -d "$staging" || -L "$staging" ]]; then
    _deploy_tree_block "staging $HOME/$staging is missing"; return 5
  fi
  if [[ "$link" == 1 ]]; then _deploy_tree_switch_link "$expected"; else _deploy_tree_switch_directory; fi
}

_deploy_tree_remote_verify() { [[ -d "$dest" ]] && _deploy_tree_snapshot "$dest"; }

# A layout (a) staging name holds the previous tree once the exchange happened, even one with
# identical bytes, so it is removed only while it is the very directory prepare created
# ($staging_id = device:inode printed by prepare; unset means never).
_deploy_tree_discard_ours() {
  [[ -d "$staging" && ! -L "$staging" && "$(stat -c %d:%i -- "$staging")" == "${staging_id:-none}" ]] \
    && rm -rf -- "$staging"
}

# After a failed switch call: remove this run's staging and link temp, and print "unchanged"
# only when the active path cannot have switched. A --link staging dir is never published,
# and the link names this run's generation only after the flip.
_deploy_tree_remote_discard() {
  local now=""
  if [[ "$link" != 1 ]]; then _deploy_tree_discard_ours && echo unchanged; return 0; fi
  rm -rf -- "$staging" && rm -f -- "$link_tmp" || return 1
  if [[ -L "$dest" ]]; then now="$(readlink -- "$dest")" || return 1; fi
  [[ "$now" == "$target" ]] || echo unchanged
}

_deploy_tree_remote_rollback() { # <previous link target, empty when this run created it>
  local why="post-switch verification failed"
  if ! _deploy_tree_lock "$lock" "$wait"; then
    _deploy_tree_block "$why and lock $HOME/$lock is busy; $HOME/$dest keeps this run's tree"
    return 7
  fi
  if [[ "$link" == 1 ]]; then
    if [[ ! -L "$dest" || "$(readlink -- "$dest")" != "$target" ]]; then
      _deploy_tree_block "$why; $HOME/$dest no longer points at $target, left unchanged"; return 7
    fi
    if [[ -z "$1" ]]; then
      rm -f -- "$dest" || { _deploy_tree_block "$why; cannot remove link $HOME/$dest"; return 7; }
      _deploy_tree_block "$why; removed new link $HOME/$dest; generation $HOME/$gen left in place"
      return 5
    fi
    if ! ln -s -- "$1" "$link_tmp"; then
      _deploy_tree_block "$why; cannot create $HOME/$link_tmp to restore $1"; return 7
    fi
    if ! mv -T -- "$link_tmp" "$dest"; then
      rm -f -- "$link_tmp"; _deploy_tree_block "$why; cannot restore $HOME/$dest -> $1"; return 7
    fi
    _deploy_tree_block "$why; restored $HOME/$dest -> $1; generation $HOME/$gen left in place"
    return 5
  fi
  if [[ -d "$backup" && ! -L "$backup" ]]; then
    if ! _deploy_tree_rename exchange "$backup" "$dest"; then
      _deploy_tree_block "$why; swap-back failed, previous tree kept at $HOME/$backup"; return 7
    fi
    rm -rf -- "$backup" # holds this run's tree after the swap-back
  elif _deploy_tree_rename noreplace "$dest" "$staging"; then
    rm -rf -- "$staging" # this run created <dest>; it existed nowhere before
  else
    _deploy_tree_block "$why; cannot remove this run's tree at $HOME/$dest"; return 7
  fi
  _deploy_tree_block "$why; restored the previous state of $HOME/$dest, removed this run's tree"
  return 5
}
