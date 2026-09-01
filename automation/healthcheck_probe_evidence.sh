#!/usr/bin/env bash
# Per-probe evidence for healthcheck failures. Sourced by healthcheck.sh.
#
# The row intentionally contains no command output, URL, account, or SSH target:
# healthcheck diagnostics can carry secrets, while the probe name, rc, latency, and
# failure boundary are enough to attribute a recurrence.

healthcheck_probe_now_ms() {
  local value
  value="$(date +%s%3N 2>/dev/null)" || return 1
  [[ "$value" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$value"
}

healthcheck_probe_failure_class() {
  local probe_rc="$1"
  case "$probe_rc" in
    0) printf 'none\n' ;;
    # timeout wraps SSH and ssh itself reports transport failures as 255.
    124|255) printf 'transport\n' ;;
    *) printf 'service\n' ;;
  esac
}

healthcheck_record_probe_evidence() {
  local check_name="$1" probe_type="$2" probe_rc="$3" elapsed_ms="$4"
  local evidence_dir recorded_at failure_class

  # The live inventory owns these values. Refuse arbitrary strings so no caller can
  # turn this private evidence sink into a secret-bearing log.
  [[ "$check_name" =~ ^[a-zA-Z0-9_.@:/[:space:]-]+$ ]] || return 0
  [[ "$probe_type" =~ ^[a-z0-9_]+$ && "$probe_rc" =~ ^[0-9]+$ && "$elapsed_ms" =~ ^[0-9]+$ ]] || return 0
  evidence_dir="${HEALTHCHECK_EVIDENCE_DIR:-${LOG_DIR:-}}"
  [[ -n "$evidence_dir" ]] || return 0
  recorded_at="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ 2>/dev/null)" || return 0
  failure_class="$(healthcheck_probe_failure_class "$probe_rc")" || return 0

  ( umask 077
    mkdir -p -m 700 -- "$evidence_dir" && chmod 700 -- "$evidence_dir" &&
      printf '{"recorded_at":"%s","probe":"%s","rc":%s,"elapsed_ms":%s,"failure_class":"%s"}\n' \
        "$recorded_at" "$check_name" "$probe_rc" "$elapsed_ms" "$failure_class" \
        >> "$evidence_dir/probe-evidence.jsonl" &&
      chmod 600 -- "$evidence_dir/probe-evidence.jsonl"
  ) >/dev/null 2>&1 || :
  return 0
}

# Run the existing probe dispatcher unchanged, then append evidence without letting a
# clock or filesystem failure alter the dispatcher's stdout, verdict, or exit code.
healthcheck_run_check_with_evidence() {
  local definition="$1" check_name probe_type node account target
  local started_ms ended_ms elapsed_ms=0 probe_rc=0

  IFS='|' read -r check_name probe_type node account target <<< "$definition"
  started_ms="$(healthcheck_probe_now_ms)" || started_ms=""
  run_check "$definition" || probe_rc=$?
  ended_ms="$(healthcheck_probe_now_ms)" || ended_ms=""
  if [[ "$started_ms" =~ ^[0-9]+$ && "$ended_ms" =~ ^[0-9]+$ ]] && (( ended_ms >= started_ms )); then
    elapsed_ms=$(( ended_ms - started_ms ))
  fi
  healthcheck_record_probe_evidence "$check_name" "$probe_type" "$probe_rc" "$elapsed_ms" || :
  return "$probe_rc"
}
