#!/usr/bin/env bash
set -euo pipefail

case "${AUTOPHAGY_DEMO_SECRET:-}" in
  DUMMY-*) ;;
  *) echo "SCENARIO-REFUSED non-dummy secret" >&2; exit 1 ;;
esac

work="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$work"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

export PATENT_DRAFT_ROOT="$tmp/agent/patent-drafts"
export PATENT_STATUS_ROOT="$tmp/status"
cli=(python3 -I "$work/scripts/patent_cli.py")

"${cli[@]}" create --slug demo-disclosure
"${cli[@]}" checklist --slug demo-disclosure --state in-progress
printf 'Synthetic local response.\n' > "$tmp/response.md"
draft_out="$("${cli[@]}" draft --slug demo-disclosure --response-file "$tmp/response.md")"
grep -Fq 'provider=openai-codex' <<<"$draft_out"
grep -Fq 'model=gpt-5.4' <<<"$draft_out"
grep -Fq 'tag_auto_attached=true' <<<"$draft_out"
test "$(stat -c '%a' "$PATENT_DRAFT_ROOT/demo-disclosure")" = 700
test "$(stat -c '%a' "$PATENT_DRAFT_ROOT/demo-disclosure/draft.md")" = 600
! grep -Fq 'Synthetic local response.' "$PATENT_STATUS_ROOT/demo-disclosure.json"
grep -Fq '"slug": "demo-disclosure"' "$PATENT_STATUS_ROOT/demo-disclosure.json"

# --- 승인형 암호화 Drive 백업 반출 게이트 (Discord stub 없음; fail-closed 경로만 검증) ---
export PATENT_EXPORT_ROOT="$tmp/export"
export PATENT_ARCHIVE_FOLDER_ID=folder-demo
export INTEROP_CONFIG="$tmp/interop.json"
printf '{"owner_id":"1","personal_approvals_channel_id":"2"}\n' > "$INTEROP_CONFIG"
export PATENT_SSH_PUBKEY="$tmp/id_ed25519.pub"
printf 'ssh-ed25519 AAAAdemo demo\n' > "$PATENT_SSH_PUBKEY"
printf '#!/usr/bin/env bash\necho called >> "%s/gws-calls.log"\n' "$tmp" > "$tmp/gws_stub.sh"
printf '#!/usr/bin/env bash\necho called >> "%s/age-calls.log"\n' "$tmp" > "$tmp/age_stub.sh"
chmod +x "$tmp/gws_stub.sh" "$tmp/age_stub.sh"
export PATENT_GWS_BIN="$tmp/gws_stub.sh"
export PATENT_AGE_BIN="$tmp/age_stub.sh"

# fail-closed: 승인 매니페스트가 없으면 execute는 거부하고 업로드/암호화를 전혀 시도하지 않는다
if "${cli[@]}" export-execute --slug demo-disclosure >/dev/null 2>"$tmp/exec_err"; then echo 'SCENARIO-FAIL export executed without a manifest' >&2; exit 1; fi
grep -Fq PATENT-PREP-REFUSED "$tmp/exec_err"
! grep -Fq 'Synthetic local response.' "$tmp/exec_err"
test ! -f "$tmp/gws-calls.log"
test ! -f "$tmp/age-calls.log"

echo "SCENARIO-PASS private=true metadata_only=true codex=true tag_auto_attached=true export_fail_closed=true"
