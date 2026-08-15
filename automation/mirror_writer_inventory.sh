#!/usr/bin/env bash
set -euo pipefail

readonly MIRROR="${1:-/srv/autophagy-agents}"

die() { printf '[mirror-writer-inventory] ERROR: %s\n' "$1" >&2; exit 2; }

(( $# <= 1 )) || die "usage: mirror_writer_inventory.sh [mirror-checkout]"
git_dir="$(git -C "$MIRROR" rev-parse --absolute-git-dir 2>/dev/null)" \
  || die "not a git checkout: $MIRROR"
[[ -d "$git_dir" ]] || die "git directory is unavailable: $git_dir"

declare -A counts=()
while IFS= read -r -d '' path; do
  owner="$(stat -c '%U' -- "$path")" || die "cannot read owner: $path"
  counts["$owner"]=$(( ${counts["$owner"]:-0} + 1 ))
done < <(find "$git_dir" -xdev -type f -print0)

printf 'owner\tfiles\n'
while IFS= read -r owner; do
  printf '%s\t%s\n' "$owner" "${counts["$owner"]}"
done < <(printf '%s\n' "${!counts[@]}" | LC_ALL=C sort)
