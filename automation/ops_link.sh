#!/usr/bin/env bash
# automation/ops_link.sh — connect a public development checkout to the private ops repo.
#
# Session evidence (.omo) and wave QA (docs/qa) live in a separate private repository so
# they never accumulate in the public one. This links them into the checkout, keeps git
# from ever seeing the links, and installs the leak-gate hooks. The ops path is recorded in
# the checkout's git config (autophagy.opsRepo), never in a tracked file: it is one
# installation's value.
#
# Usage: automation/ops_link.sh [<ops-repo-path>]   (default: git config autophagy.opsRepo)
set -euo pipefail

die() { printf '[ops-link] %s\n' "$1" >&2; exit "${2:-1}"; }
log() { printf '[ops-link] %s\n' "$*"; }

root="$(git rev-parse --show-toplevel)" || die "not inside a git checkout"
ops="${1:-$(git -C "$root" config --get autophagy.opsRepo || true)}"
[[ -n "$ops" ]] || die "no ops repository given and git config autophagy.opsRepo is unset"
ops="$(cd "$ops" 2>/dev/null && pwd)" || die "ops repository not found: ${1:-<config>}"
git -C "$ops" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repository: $ops"
for need in omo qa leak/denylist.txt; do
  [[ -e "$ops/$need" ]] || die "ops repository lacks $need: $ops"
done
[[ "$(cd "$root" && pwd)" != "$ops" ]] || die "refusing to link the ops repository into itself"

# The links must never be committed. `.omo/` (trailing slash) matches directories only,
# and git sees a symlink as a file, so the patterns carry no trailing slash.
common="$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)"
exclude="$common/info/exclude"
mkdir -p "$(dirname "$exclude")"
for pattern in /.omo /docs/qa; do
  grep -qxF -- "$pattern" "$exclude" 2>/dev/null || printf '%s\n' "$pattern" >>"$exclude"
done

link() { # link <checkout-relative path> <target>
  local path="$root/$1" target="$2"
  if [[ -L "$path" ]]; then
    [[ "$(readlink -- "$path")" == "$target" ]] && return 0
    die "$1 is a link to somewhere else: $(readlink -- "$path")"
  fi
  [[ ! -e "$path" ]] || die "$1 already exists as a real path; move it into $target first"
  if git -C "$root" ls-files --error-unmatch -- "$1" >/dev/null 2>&1; then
    die "$1 is tracked in this checkout; this is the private source, not a public checkout"
  fi
  mkdir -p "$(dirname "$path")"
  ln -s -- "$target" "$path"
  log "linked $1 -> $target"
}
link .omo "$ops/omo"
link docs/qa "$ops/qa"

git -C "$root" config autophagy.opsRepo "$ops"
git -C "$root" config autophagy.publicGate enforce
install -d "$common/hooks"
install -m 755 "$root/automation/hooks/public-gate-pre-commit" "$common/hooks/pre-commit"
install -m 755 "$root/automation/hooks/public-gate-commit-msg" "$common/hooks/commit-msg"
install -m 755 "$root/automation/hooks/pre-push" "$common/hooks/pre-push"
log "READY $root (ops $ops; hooks pre-commit, commit-msg, pre-push)"
