#!/usr/bin/env bash
# Is the node's mailon runtime still the vendor code that origin/main carries?
#
# WHY (2026-07-29 ~ 2026-08-18): the mailon runtime is only refreshed when a human runs
# skills/mail/deploy.sh. A fix committed on 07-29 did not reach production until 08-18 —
# **19 days** — and during those 19 days nothing anywhere said so. Skills are judged by
# `readlink live/<skill>` and code converges through the reconciler, but the vendor
# runtime is neither. Worse, the eventual deploy shipped 19 days of unexercised change
# in one go: two defects landed together and every send failed immediately.
#
# This probe closes the observation gap only. It never deploys, never restarts anything,
# and reads nothing that needs credentials.
#
#   mailon_runtime_drift.sh
#     exit 0  runtime matches the release tree's vendor digest
#     exit 1  DRIFT — the runtime is pinned to older (or other) vendor code
#     exit 2  UNKNOWN — cannot judge (no runtime, no vendor tree, broken link)
#
# 부재는 PASS 가 아니다: every "cannot tell" path exits 2, never 0.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$here/mailon_vendor_digest.sh" 2>/dev/null || {
  printf 'UNKNOWN mailon-runtime-drift: digest helper missing beside %s\n' "$here"
  exit 2
}

release_root="${AUTOPHAGY_REPO_ROOT:-}"
if [ -z "$release_root" ]; then
  if [ -d /srv/autophagy-agent-current/skills ]; then
    release_root=/srv/autophagy-agent-current
  else
    release_root=/srv/autophagy-agents
  fi
fi
runtime_root="${MAILON_RUNTIME_ROOT:-$HOME/.hermes/mailon-runtime}"

vendor_tree="$release_root/skills/mail/vendor/mailon"
if [ ! -d "$vendor_tree" ]; then
  printf 'UNKNOWN mailon-runtime-drift: no vendor tree at %s\n' "$vendor_tree"
  exit 2
fi

current="$runtime_root/current"
if [ ! -e "$current" ]; then
  printf 'UNKNOWN mailon-runtime-drift: no runtime release at %s\n' "$current"
  exit 2
fi

deployed="$(basename "$(readlink -f "$current" 2>/dev/null || true)")"
if [ -z "$deployed" ] || [ "$deployed" = "/" ]; then
  printf 'UNKNOWN mailon-runtime-drift: %s does not resolve\n' "$current"
  exit 2
fi

expected="$(mailon_vendor_digest "$vendor_tree")" || {
  printf 'UNKNOWN mailon-runtime-drift: digest of %s failed\n' "$vendor_tree"
  exit 2
}

if [ "$deployed" = "$expected" ]; then
  printf 'OK mailon-runtime-drift: runtime %s matches %s\n' "$deployed" "$release_root"
  exit 0
fi

printf 'DRIFT mailon-runtime-drift: runtime=%s repo=%s — run skills/mail/deploy.sh (owner-approved) to converge\n' \
  "$deployed" "$expected"
exit 1
