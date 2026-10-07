#!/usr/bin/env bash
# Re-run, at release time, the standing probes for surfaces a release cannot converge
# (root helpers, the healthcheck allowlist wrapper, the RAG stack) and print their verdicts.
#
# Why re-run instead of reading the healthcheck incident state: that file holds incident
# names only — no release sha, no observation time — so a drift this release introduced
# can be missing from it, and a name alone does not say which provisioner fixes the asset.
# The probe's own lines do, so they are passed through verbatim.
#
# Sourcing the entry is safe: healthcheck.sh calls main only when executed directly, so no
# lock, log directory, notification or ticket is created. Only the existing run_check
# dispatcher runs, so no remote command is added and the allowlist is unchanged.
#
# Output (stdout): DELEGATED|<probe type>|<node>|<PASS|FAIL|UNKNOWN>|<check name>, then one
# DELEGATED-DETAIL|<probe type>|<node>|<line> per probe output line, and DELEGATED-END|<n>.
# A non-zero exit or a missing/mismatched END means "could not tell" to the caller.

entry="${HEALTHCHECK_DELEGATED_ENTRY:-$(dirname "${BASH_SOURCE[0]}")/healthcheck.sh}"
# shellcheck source=automation/healthcheck.sh
source "$entry" >&2 || exit 1
set +e

count=0
for definition in "${LIVE_CHECKS[@]}"; do
  IFS='|' read -r name probe node _rest <<< "$definition"
  case "$probe" in
    release_helper_drift|healthcheck_wrapper_current|rag_stack_current) ;;
    *) continue ;;
  esac
  output="$(run_check "$definition" 2>&1 < /dev/null)"
  rc=$?
  status=PASS
  if (( rc != 0 )); then
    status=FAIL
    [[ "$output" == *-UNKNOWN* ]] && status=UNKNOWN
  fi
  printf 'DELEGATED|%s|%s|%s|%s\n' "$probe" "$node" "$status" "$name"
  while IFS= read -r line; do
    [[ -n "$line" ]] && printf 'DELEGATED-DETAIL|%s|%s|%s\n' "$probe" "$node" "$line"
  done <<< "$output"
  count=$((count + 1))
done
printf 'DELEGATED-END|%s\n' "$count"
