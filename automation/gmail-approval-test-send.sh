#!/usr/bin/env bash
# One-time W0-6 acceptance helper. Run only as the GWS credential owner (agent).
set -euo pipefail
umask 077

usage() {
  cat <<'USAGE'
Usage:
  gmail-approval-test-send.sh --to <own-google-address> --approval-message-id <Discord-message-ID>

Preconditions:
  1. cha manually posted the request in #approvals.
  2. cha manually added the explicit ✅ approval reaction to that message.
  3. The message ID supplied here is that approval-request message.

This helper sends exactly one message to the authenticated GWS account itself. It
does not read Discord or create a reaction; W1-2 has not installed that bot loop.
USAGE
}

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

recipient=''
approval_message_id=''

while (($#)); do
  case "$1" in
    --to)
      (($# >= 2)) || fail '--to requires an email address'
      recipient=$2
      shift 2
      ;;
    --approval-message-id)
      (($# >= 2)) || fail '--approval-message-id requires a Discord message ID'
      approval_message_id=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[[ $recipient =~ ^[^[:space:]@,]+@[^[:space:]@,]+\.[^[:space:]@,]+$ ]] ||
  fail 'recipient must be one email address'
[[ $approval_message_id =~ ^[0-9]{17,20}$ ]] ||
  fail 'approval message ID must be a Discord snowflake (17-20 decimal digits)'

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(cd -- "$script_dir/.." && pwd -P)
approval_log=${APPROVAL_LOG_PATH:-"$repo_root/logs/approvals.jsonl"}
approval_log_dir=$(dirname -- "$approval_log")

[[ -d $approval_log_dir && -w $approval_log_dir ]] ||
  fail "approval log directory is not agent-writable: $approval_log_dir"
touch -- "$approval_log" || fail 'cannot preflight approval log write'
chmod 600 -- "$approval_log"
command -v flock >/dev/null || fail 'flock is required for atomic approval-log appends'

# gws auth status emits account metadata, so capture it rather than printing it.
auth_status=$(gws auth status 2>/dev/null) || fail 'gws authentication is unavailable or invalid'
self_email=$(printf '%s' "$auth_status" | python3 -c '
import json
import sys

text = sys.stdin.read()
decoder = json.JSONDecoder()
for offset, character in enumerate(text):
    if character != "{":
        continue
    try:
        value, _ = decoder.raw_decode(text[offset:])
    except json.JSONDecodeError:
        continue
    user = value.get("user") if isinstance(value, dict) else None
    if isinstance(user, str) and user:
        print(user)
        break
else:
    raise SystemExit(1)
') || fail 'could not determine the authenticated GWS account'

[[ ${recipient,,} == ${self_email,,} ]] ||
  fail 'refusing non-self recipient; --to must equal the authenticated GWS account'

printf 'Confirm that cha manually added ✅ to Discord approval message %s.\n' "$approval_message_id"
read -r -p 'Type SEND-SELF-TEST to execute the one self-addressed email: ' confirmation
[[ $confirmation == SEND-SELF-TEST ]] || fail 'manual confirmation did not match; no email sent'

requested_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
target_id="gmail:self-test:$recipient"
subject="W0-6 Gmail approval-gated self-send"
body="W0-6 acceptance self-send. Approval message ID: $approval_message_id. Requested at: $requested_at."

gws_stderr=$(mktemp "${TMPDIR:-/tmp}/w0-6-gmail-send.XXXXXX")
trap 'rm -f -- "$gws_stderr"' EXIT
if ! gws gmail +send --to "$recipient" --subject "$subject" --body "$body" \
  >/dev/null 2>"$gws_stderr"; then
  fail 'gws Gmail send failed; no approval-log entry was written'
fi

completed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
approval_entry=$(python3 - "$completed_at" "$target_id" "$approval_message_id" "$subject" "$body" <<'PY'
import hashlib
import json
import sys

timestamp, target_id, message_id, subject, body = sys.argv[1:]
hashed_payload = {
    "action": "gmail.send.self_test",
    "approval_message_id": message_id,
    "body": body,
    "subject": subject,
    "target_id": target_id,
}
digest = hashlib.sha256(
    json.dumps(hashed_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
entry = {
    "timestamp": timestamp,
    "target_id": target_id,
    "hash": f"sha256:{digest}",
    "action": "gmail.send.self_test",
    "approval": {
        "channel": "approvals",
        "message_id": message_id,
        "method": "manual_reaction",
    },
    "result": {"status": "sent"},
}
print(json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
PY
)

{
  flock -x 9
  printf '%s\n' "$approval_entry" >&9
} 9>>"$approval_log"

python3 - "$approval_log" "$approval_message_id" <<'PY'
import datetime as dt
import json
import re
import sys

path, expected_message_id = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    lines = [line for line in handle if line.strip()]
assert lines, "approval log is empty"
entry = json.loads(lines[-1])
assert isinstance(entry.get("timestamp"), str) and entry["timestamp"].endswith("Z")
dt.datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
assert isinstance(entry.get("target_id"), str) and entry["target_id"]
assert re.fullmatch(r"sha256:[0-9a-f]{64}", entry.get("hash", ""))
assert entry.get("action") == "gmail.send.self_test"
assert entry.get("approval", {}).get("channel") == "approvals"
assert entry.get("approval", {}).get("message_id") == expected_message_id
assert entry.get("approval", {}).get("method") == "manual_reaction"
assert entry.get("result", {}).get("status") == "sent"
PY

printf 'PASS Gmail self-send completed; approvals.jsonl schema valid.\n'
