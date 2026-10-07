#!/usr/bin/env bash
# automation/deploy_tree.sh — replace a home-relative runtime tree so that a reader never
# sees a missing path or a half-extracted tree (RCB todo 9, GAP-11). The old deployers did
# `rm -rf; mkdir; tar -x` in place: a cron tick starting in that window imported nothing,
# or half a package.
#
# Usage (source it; the caller defines `run_agent <script>` and `repo_root`):
#   source "$repo_root/automation/deploy_tree.sh"
#   deploy_tree_swap [--link] [--lock <home-rel-lock>] [--prefix <dir>] \
#     <base-dir> <home-rel-dest> [<path>...]
# Without <path>... the selection is the python profile of the standing runtime package
# probe (automation/runtime_package_probe.sh): tracked *.py under <base-dir> minus the
# top-level cron/. Selection is always tracked-only, so a dirty worktree ships nothing new.
#
# Three remote calls, split so a test can park a reader between them:
#   1 prepare  extract into a private staging dir, print its snapshot (compared locally)
#   2 switch   ONE atomic call changes the active path (under --lock when given)
#   3 verify   re-snapshot <dest>; on mismatch a fourth call restores the previous state
# Layout (a), default: <dest> stays a real directory. renameat2(RENAME_EXCHANGE) swaps it
#   with staging, and the displaced tree is kept as <dest>.old.<UTC>-<pid>. Trees in the
#   standing runtime package table must use this layout (its find skips a symlink root).
# Layout (b), --link: <dest> is a symlink to <dest>.d/<UTC>-<pid>. The generation is
#   verified BEFORE the link flips. If the post-switch check fails, the link flips back,
#   but the published generation is kept: a reader may have pinned it with realpath.
# Earlier backups, generations and foreign paths are never deleted or moved.
# rc: 0 deployed · 5 blocked (active path unchanged or restored) · 6 lock busy ·
#   7 state unknown: a switch or rollback call was cut, or a step could not be undone, so
#   <dest> may hold this run's unverified tree; inspect it before redeploying (RCB todo 48).
#   Every non-zero rc is a failed deploy.
# Calling convention: call it plainly from a script under `set -euo pipefail` (no `|| true`
#   needed); deploy_tree_swap turns errexit off for its own body (`local -`, bash >= 4.4) and
#   checks every step itself, so a failing remote call (ssh 255) is answered, not fatal: a
#   failed prepare call removes staging (rc 5); a failed switch call is followed by one
#   discard call that removes staging only while it is still the very directory prepare
#   created (device:inode, not content: in layout (a) an exchange leaves the previous tree
#   there, possibly with identical bytes, and it stays as the backup) plus this run's link
#   temp, and returns 5 only when nothing can have switched, else 7; a failed verify call is
#   rolled back (5), and a failed
#   rollback call is 7. A cleanup call that itself fails is reported, not retried. The
#   caller's options are restored on return; a non-zero return then trips the caller's
#   errexit (RCB todo 45). The node-side functions live in automation/deploy_tree_remote.sh.
# Env: DEPLOY_TREE_LOCK_WAIT  seconds to wait for --lock (default 300)

_DEPLOY_TREE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=automation/deploy_provenance.sh
source "$_DEPLOY_TREE_DIR/deploy_provenance.sh"
# The node-side half (shipped by `declare -f` below): snapshot, rename, lock, the remote steps.
# shellcheck source=automation/deploy_tree_remote.sh
source "$_DEPLOY_TREE_DIR/deploy_tree_remote.sh"

# Relative, non-empty, and free of empty, "." and ".." components.
_deploy_tree_relative_ok() {
  [[ -n "$1" && "$1" != /* && "/$1/" != *//* && "/$1/" != */./* && "/$1/" != */../* ]]
}

# The same snapshot computed from the local source files that will be shipped.
_deploy_tree_expected() ( # <base-dir> <prefix> <file>...
  set -o pipefail
  base="$1" prefix="$2"
  shift 2
  printf '%s\0' "$@" | LC_ALL=C sort -z | while IFS= read -r -d '' file; do
    case "/${prefix:+$prefix/}$file" in */__pycache__/*|*/.venv/*|*/.ruff_cache/*|*/.pytest_cache/*) continue ;; esac
    hash="$(sha256sum < "$base/$file")" || exit 1
    printf '%s|%s\n' "${hash%% *}" "${prefix:+$prefix/}$file"
  done
)

# --link would blind the standing probe (its find skips a symlink root), so any destination
# overlapping a row of the runtime package table is refused; an unreadable table refuses too.
_deploy_tree_probe_watched() { # <home-rel-dest>
  local table="$_DEPLOY_TREE_DIR/../configs/runtime-package-manifest.txt" account source runtime rest
  [[ -r "$table" ]] || return 0
  while IFS='|' read -r account source runtime rest; do
    [[ -z "$account" || "$account" == \#* || -z "$runtime" ]] && continue
    [[ "$runtime/" == "$1/"* || "$1/" == "$runtime/"* ]] && return 0
  done < "$table"
  return 1
}

_deploy_tree_select() { # <base-dir> [<path>...] -> NUL list, tracked files only
  local base="$1" file
  shift
  if [[ $# -gt 0 ]]; then
    git --literal-pathspecs -C "$base" ls-files --error-unmatch -- "$@" >/dev/null 2>&1 || return 1
    git --literal-pathspecs -C "$base" ls-files -z -- "$@"
    return
  fi
  git -C "$base" ls-files -z | while IFS= read -r -d '' file; do
    [[ "$file" == *.py && "$file" != cron/* ]] && printf '%s\0' "$file"
  done
  return "${PIPESTATUS[0]}"
}

deploy_tree_swap() {
  # The caller's errexit would end the shell on a failed remote call before the cleanup runs.
  local - link=0 lock="" prefix="" wait="${DEPLOY_TREE_LOCK_WAIT:-300}" base dest path
  set +e
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --link) link=1; shift ;;
      --lock|--prefix)
        [[ $# -ge 2 ]] || { _deploy_tree_block "$1 needs a value"; return 5; }
        if [[ "$1" == --lock ]]; then lock="$2"; else prefix="$2"; fi
        shift 2 ;;
      --) shift; break ;;
      -*) _deploy_tree_block "unknown option $1"; return 5 ;;
      *) break ;;
    esac
  done
  [[ $# -ge 2 ]] || { _deploy_tree_block "usage: deploy_tree_swap [--link] [--lock L] [--prefix D] <base-dir> <dest> [<path>...]"; return 5; }
  base="$1" dest="$2"
  shift 2
  if ! declare -F run_agent >/dev/null || [[ -z "${repo_root:-}" || ! -d "$repo_root" ]]; then
    _deploy_tree_block "caller must define run_agent and repo_root"; return 5
  fi
  for path in "$dest" ${lock:+"$lock"} ${prefix:+"$prefix"} "$@"; do
    _deploy_tree_relative_ok "$path" || { _deploy_tree_block "refusing path '$path'"; return 5; }
  done
  [[ "$wait" =~ ^[0-9]+$ ]] || { _deploy_tree_block "DEPLOY_TREE_LOCK_WAIT is not a number"; return 5; }
  if ((link)) && _deploy_tree_probe_watched "$dest"; then
    _deploy_tree_block "$dest is read by the standing runtime package probe; use the directory layout"; return 5
  fi
  [[ "$base" == /* ]] || base="$repo_root/$base"
  local toplevel top_real root_real
  if [[ ! -d "$base" ]] || ! toplevel="$(git -C "$base" rev-parse --show-toplevel 2>/dev/null)" \
    || ! top_real="$(realpath -- "$toplevel")" || ! root_real="$(realpath -- "$repo_root")" \
    || [[ "$top_real" != "$root_real" ]]; then
    _deploy_tree_block "base dir $base is not a directory of $repo_root"; return 5
  fi
  local -a files=()
  mapfile -d '' -t files < <(_deploy_tree_select "$base" "$@")
  wait "$!" || { _deploy_tree_block "selection under $base failed (untracked path?)"; return 5; }
  ((${#files[@]})) || { _deploy_tree_block "nothing selected under $base"; return 5; }
  for path in "${files[@]}"; do
    [[ -f "$base/$path" && ! -L "$base/$path" ]] || { _deploy_tree_block "selected file $path is not a regular file"; return 5; }
  done

  local expected stamp pid staging staging_id backup="" gen="" link_tmp="" target="" prelude out rc
  # A fresh subshell's pid: two calls from one shell within one second still get distinct names.
  pid="$(printf '%s' "$BASHPID")"
  expected="$(_deploy_tree_expected "$base" "$prefix" "${files[@]}")" || { _deploy_tree_block "cannot hash the local selection"; return 5; }
  stamp="$(date -u +%Y%m%dT%H%M%SZ)" || return 5
  if ((link)); then
    staging="$dest.d/.staging.$pid" gen="$dest.d/$stamp-$pid" link_tmp="$dest.link.$pid"
    target="${dest##*/}.d/$stamp-$pid"
  else
    staging="$dest.staging.$pid" backup="$dest.old.$stamp-$pid"
  fi
  prelude="set -o pipefail; cd \"\$HOME\" || exit 4; umask 077
$(declare -f _deploy_tree_block _deploy_tree_snapshot _deploy_tree_rename _deploy_tree_lock \
  _deploy_tree_remote_prepare _deploy_tree_switch_directory _deploy_tree_switch_link \
  _deploy_tree_remote_switch _deploy_tree_remote_verify _deploy_tree_discard_ours \
  _deploy_tree_remote_discard _deploy_tree_remote_rollback)
$(printf 'link=%q dest=%q staging=%q backup=%q gen=%q link_tmp=%q target=%q lock=%q wait=%q prefix=%q' \
  "$link" "$dest" "$staging" "$backup" "$gen" "$link_tmp" "$target" "$lock" "$wait" "$prefix")"

  # 1 prepare. rc 5 = refused before creating anything; anything else may leave staging.
  # The first output line is the staging dir's identity, which later calls compare against.
  out="$(set -o pipefail; deploy_archive_stream "$repo_root" "$base" "${files[@]}" \
    | run_agent "$prelude"$'\n_deploy_tree_remote_prepare')"
  rc=$?
  ((rc == 5)) && return 5
  staging_id="${out%%$'\n'*}" out="${out#*$'\n'}"
  if ((rc != 0)) || [[ ! "$staging_id" =~ ^id=[0-9]+:[0-9]+$ || "$out" != "$expected" ]]; then
    _deploy_tree_block "staging mismatch for $dest (rc=$rc); active tree untouched"
    run_agent "$prelude"$'\nrm -rf -- "$staging"' </dev/null \
      || _deploy_tree_block "could not remove staging $staging"
    return 5
  fi
  prelude+=$'\n'"staging_id=${staging_id#id=}"

  # 2 switch. The remote side cleans up after itself on every refusal (rc 5/6/7); any other
  # rc is the transport (ssh 255): the remote may or may not have switched, so ask it.
  out="$(printf '%s\n' "$expected" | run_agent "$prelude"$'\n_deploy_tree_remote_switch')"
  rc=$?
  ((rc == 6)) && return 6
  ((rc == 5)) && { _deploy_tree_block "switch of $dest refused (rc=5)"; return 5; }
  ((rc == 7)) && { _deploy_tree_block "switch of $dest stopped on the node with its state unknown (rc=7)"; return 7; }
  if ((rc != 0)); then
    out="$(run_agent "$prelude"$'\n_deploy_tree_remote_discard' </dev/null)"
    [[ "$out" == unchanged ]] && { _deploy_tree_block "switch call for $dest failed (rc=$rc); nothing switched, staging removed"; return 5; }
    _deploy_tree_block "switch call for $dest failed (rc=$rc): state unknown, it may hold this run's unverified tree"
    return 7
  fi
  local previous=""
  if ((link)); then
    [[ "$out" == previous=* && "$out" != *$'\n'* ]] || {
      _deploy_tree_block "switch output for $dest is unreadable; link state unknown"; return 7; }
    previous="${out#previous=}"
  fi

  # 3 verify through the active path; on mismatch restore in one atomic call.
  out="$(run_agent "$prelude"$'\n_deploy_tree_remote_verify' </dev/null)"
  rc=$?
  ((rc == 0)) && [[ "$out" == "$expected" ]] && return 0
  run_agent "$prelude"$'\n'"_deploy_tree_remote_rollback $(printf '%q' "$previous")" </dev/null
  rc=$?
  ((rc == 5)) && return 5
  _deploy_tree_block "rollback of $dest did not complete (rc=$rc): state unknown, it may hold this run's unverified tree"
  return 7
}
