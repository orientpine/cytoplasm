#!/usr/bin/env bash
# What an installation should declare, told by the installation itself.
#
# The declaration shipped with documentation and nothing else, so a new operator had to
# read the guide, work out which service groups their installation runs, and translate that
# into a variable. Only the last step is mechanical. The installation that prompted this had
# already worked the answer out the hard way — by watching probes fail for services it never
# installed (docs/troubleshooting/신규-노드-설치-공백.md §5).
#
# This uses ONLY probe commands the SSH forced-command allowlist already carries, so an
# operator can run it before regenerating anything. It writes nothing, tickets nothing and
# notifies nobody.
#
# It also never claims certainty it does not have: `systemctl is-active` answers "inactive"
# both for a unit nobody installed and for one that died a minute ago. A group whose probes
# all stay silent is therefore a QUESTION put to the operator, not a conclusion. A group
# that answered in part is not even a question — the service is there and something is wrong
# with it, and offering to stop watching it would turn an outage into a configuration change.

#: Pure: per-group tallies on stdin as `group failed total`, advice on stdout.
healthcheck_suggest_render() {
  local -a silent=() present=() optional_silent=()
  local group failed total core_silent=0 remote_failed=0 remote_total=0

  printf '[healthcheck] what this installation answered:\n'
  while read -r group failed total; do
    if [[ -z "$group" ]]; then
      continue
    fi
    if [[ "$group" == "remote" ]]; then
      remote_failed="$failed"
      remote_total="$total"
      continue
    fi
    if (( total > 0 && failed == total )); then
      printf '  %-12s %s probe(s), none answered\n' "$group" "$total"
      silent+=("$group")
      if [[ "$group" == "core" ]]; then
        core_silent=1
      fi
    else
      printf '  %-12s %s probe(s), %s failed\n' "$group" "$total" "$failed"
      present+=("$group")
    fi
  done

  # Local core probes can pass while SSH is completely unavailable, so core's aggregate
  # is not transport evidence. Reuse the sweep's remote-only collapse rule: if every
  # remote probe failed, wrapper denials and SSH failures remain infrastructure unknowns.
  if (( remote_total > 1 && remote_failed == remote_total )); then
    printf '\n[healthcheck] every remote probe failed; suspect SSH transport or the probe allowlist.\n'
    printf '[healthcheck] no HEALTHCHECK_SERVICES declaration is suggested.\n'
    return 1
  fi

  # Core answering nothing is the same ambiguity even when some remote path answered.
  if (( core_silent == 1 )); then
    printf '\n[healthcheck] core answered nothing, so this run cannot tell what is installed.\n'
    printf '[healthcheck] suspect the shared SSH path first. Nothing is suggested.\n'
    return 0
  fi

  if (( ${#silent[@]} > 0 )); then
    for group in "${silent[@]}"; do
      if [[ "$group" != "core" ]]; then
        optional_silent+=("$group")
      fi
    done
  fi
  if (( ${#optional_silent[@]} == 0 )); then
    printf '\n[healthcheck] every group answered, so every group is installed here.\n'
    printf '[healthcheck] Leave HEALTHCHECK_SERVICES as it is. Any failure above is a\n'
    printf '[healthcheck] service problem, not a declaration problem.\n'
    return 0
  fi

  printf '\n[healthcheck] these groups answered nothing: %s\n' "${optional_silent[*]}"
  printf '[healthcheck] a probe cannot tell a service that is not installed from one that is\n'
  printf '[healthcheck] down right now, so this is your call. If this installation does not run\n'
  printf '[healthcheck] them, put this where the sweep runs (the cron environment too):\n\n'
  printf '  export HEALTHCHECK_SERVICES="%s"\n\n' "${present[*]}"
  printf '[healthcheck] then regenerate the probe allowlist wrapper on the node:\n'
  printf '  bash <release>/automation/healthcheck_probe_wrapper.sh --install <node>\n'
}

#: One line for a failing check that belongs to a group this installation may not run.
#: Silent for core, which no installation may decline — proposing that a core failure might
#: be a configuration choice would point the operator away from the thing that just broke.
healthcheck_optional_group_hint() { # healthcheck_optional_group_hint <display name>
  local check_name="$1" group=""
  group="$(healthcheck_group_of_check "$check_name")" || return 0
  if [[ "$group" == "core" ]]; then
    return 0
  fi
  printf 'HINT %s belongs to the %s group; if this installation does not run it, drop that group (healthcheck.sh --suggest)\n' \
    "$check_name" "$group"
}

#: Run every catalogued probe once and report. The CATALOG is read, not LIVE_CHECKS: the
#: operator is asking what they should declare, so a declaration already in force must not
#: hide the answer from them.
healthcheck_suggest_run() {
  local entry group definition probe_type
  local -A failed=() total=()
  local remote_failed=0 remote_total=0

  for group in "${HEALTHCHECK_SERVICE_GROUPS[@]}"; do
    failed["$group"]=0
    total["$group"]=0
  done
  for entry in "${HEALTHCHECK_CHECK_CATALOG[@]}"; do
    group="${entry%%|*}"
    definition="${entry#*|}"
    IFS='|' read -r _ probe_type _ <<< "$definition"
    total["$group"]=$(( total["$group"] + 1 ))
    if [[ " $LOCAL_PROBES " != *" $probe_type "* ]]; then
      remote_total=$(( remote_total + 1 ))
    fi
    if ! run_check "$definition" >/dev/null 2>&1; then
      failed["$group"]=$(( failed["$group"] + 1 ))
      if [[ " $LOCAL_PROBES " != *" $probe_type "* ]]; then
        remote_failed=$(( remote_failed + 1 ))
      fi
    fi
  done
  {
    for group in "${HEALTHCHECK_SERVICE_GROUPS[@]}"; do
      printf '%s %s %s\n' "$group" "${failed[$group]}" "${total[$group]}"
    done
    printf 'remote %s %s\n' "$remote_failed" "$remote_total"
  } | healthcheck_suggest_render
}
