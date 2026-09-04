#!/usr/bin/env bash
# Sandbox scenario for the plaud skill (W1-8 pipeline stage 1).
# The CLI is read-only and stdlib-only, so the whole contract is exercised here:
# absent state → reported, fixture state → counted, broken state → refused.
# MUST run with DUMMY secrets only. Refuses anything that looks real.
set -euo pipefail

fail() { printf 'SCENARIO-FAIL %s\n' "$1" >&2; exit 1; }

secret="${AUTOPHAGY_DEMO_SECRET:-}"
[[ -n "$secret" ]] || fail "AUTOPHAGY_DEMO_SECRET is not set"
[[ "$secret" == DUMMY-* ]] || fail "secret does not carry the DUMMY- prefix (real secrets are forbidden in sandbox)"
if [[ "$secret" == *sk-* || "$secret" == *ghp_* ]]; then
  fail "secret matches a real-token shape"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cli="$script_dir/plaud_cli.py"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

absent="$(python3 "$cli" status --state "$work/missing.json")"
[[ "$absent" == "PLAUD-STATUS state=absent" ]] || fail "absent state not reported: $absent"

cat > "$work/state.json" <<'JSON'
{"version": 1, "last_poll_at": "2026-09-02T08:38:52Z", "records": {
  "rec-1": {"recording_id": "rec-1", "recorded_at": "2026-09-02T04:00:00Z",
            "note_relpath": "000_PARA/Area/Lifelog/2026/2026-09-02-a--abcdef123456.md",
            "status": "posted", "channel_id": "111", "message_id": "m-1", "approval_thread_id": "111"},
  "rec-2": {"recording_id": "rec-2", "recorded_at": "2026-09-02T05:00:00Z",
            "note_relpath": "000_PARA/Area/Lifelog/2026/2026-09-02-b--abcdef654321.md",
            "status": "planned", "channel_id": "", "message_id": null, "approval_thread_id": null},
  "rec-3": {"recording_id": "rec-3", "recorded_at": "2026-09-02T06:00:00Z",
            "note_relpath": "000_PARA/Area/Lifelog/2026/2026-09-02-c--abcdef000000.md",
            "status": "transcribing", "channel_id": "", "message_id": null, "approval_thread_id": null,
            "transcribe_attempts": 1, "last_block_reason": "rc=4 로컬 전사 도구를 찾지 못했습니다"}
}}
JSON
mkdir -p "$work/transcripts"
printf '# b 전사본\n' > "$work/transcripts/2026-09-02-b--abcdef654321.md"
present="$(python3 "$cli" status --state "$work/state.json")"
grep -q '^PLAUD-STATUS state=present' <<<"$present" || fail "present state not reported"
grep -q 'posted 1' <<<"$present" || fail "posted count missing"
grep -q 'planned 1' <<<"$present" || fail "planned count missing"
grep -q 'rec-1 · 스레드 111' <<<"$present" || fail "pending card missing"
grep -q 'transcribing 1' <<<"$present" || fail "transcribing count missing"
grep -q 'rec-3 · 시도 1 · 사유 rc=4' <<<"$present" || fail "transcribing reason missing"
grep -q 'rec-2 · 2026-09-02-b--abcdef654321.md' <<<"$present" || fail "local transcript listing missing"

printf '{' > "$work/broken.json"
if python3 "$cli" status --state "$work/broken.json" 2>/dev/null; then
  fail "a broken state must not be reported as healthy"
fi

printf '%s\n' "$present"
printf 'SCENARIO-PASS secret_len=%s account=%s\n' "${#secret}" "$(whoami)"
