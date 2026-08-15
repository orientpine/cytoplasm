#!/usr/bin/env bash
# W3-3 coordination protocol E2E orchestrator (runs from the repo machine).
# Scenarios: happy (vs live peer, injected owner confirm, gated write+cleanup),
# refusal (peer declines twice → 1 renegotiation → terminate, 0 writes),
# deadlock (peer gateway stopped → short-timeout escalation DM, 0 writes).
# Production deadlock timeout is 600 s (10 min); the E2E injects 15/60 s.
set -euo pipefail
readonly HOST="<primary-node>"
readonly PEER_READY_TIMEOUT_S=90
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

agent() { ssh "$HOST" "sudo -n -u agent -H bash -lc $(printf '%q' "$1")"; }
peer() { ssh "$HOST" "sudo -n -u peer -H bash -lc $(printf '%q' "$1")"; }
peer_unit() {
  peer "XDG_RUNTIME_DIR=/run/user/\$(id -u) systemctl --user $1 hermes-gateway.service"
}
peer_gateway_log_lines() {
  peer 'if [[ -f "$HOME/.hermes/logs/gateway.log" ]]; then wc -l < "$HOME/.hermes/logs/gateway.log"; else printf 0; fi'
}
peer_gateway_connected_after() {
  local start_line="$1"
  peer "log=\"\$HOME/.hermes/logs/gateway.log\"; [[ -f \"\$log\" ]] || exit 1; lines=\$(wc -l < \"\$log\"); if (( lines <= $start_line )); then grep -Eq 'discord connected|Connected as' \"\$log\"; else tail -n +$((start_line + 1)) \"\$log\" | grep -Eq 'discord connected|Connected as'; fi"
}
wait_for_peer_discord_ready() {
  local start_line="$1" deadline=$((SECONDS + PEER_READY_TIMEOUT_S)) state
  while (( SECONDS < deadline )); do
    state="$(peer_unit is-active 2>/dev/null || true)"
    if [[ "$state" == "active" ]] && peer_gateway_connected_after "$start_line" >/dev/null 2>&1; then
      echo "gateway peer=active discord=ready"
      return 0
    fi
    sleep 2
  done
  echo "peer gateway did not reconnect to Discord within ${PEER_READY_TIMEOUT_S}s" >&2
  return 1
}
push() { ssh "$HOST" "sudo -n -u agent -H bash -c 'umask 077; cat > \$HOME/$2'" < "$1"; }

tomorrow="$(TZ=Asia/Seoul date -d '+1 day' +%F)"
range_start="${tomorrow}T09:00:00+09:00"
range_end="${tomorrow}T18:00:00+09:00"
echo "W3-3 E2E range=${range_start}..${range_end} (KST tomorrow)"

push "$repo/tests/e2e/w3_3_runner.sh" ".w33_runner.sh"
push "$repo/tests/e2e/w3_3_probe.py" ".w33_probe.py"
agent 'umask 077; openssl rand -hex 32 > "$HOME/.w33-e2e.secret"'
peer_stopped=0
cleanup() {
  if [[ "$peer_stopped" -eq 1 ]]; then
    peer_unit start >/dev/null 2>&1 || true
  fi
  agent 'rm -f "$HOME/.w33-e2e.secret" "$HOME/.w33_runner.sh" "$HOME/.w33_probe.py" "$HOME/.w33-del.json"' || true
}
trap cleanup EXIT

failed=0
cleanup_event() {
  local event_id="$1" del_out del_id
  del_out="$(python3 "$CAL" draft-delete --event-id "$event_id" --label "$SUMMARY")"
  del_id="$(sed -n 's/^DRAFT-CREATED id=\([0-9a-f]*\) .*/\1/p' <<<"$del_out")"
  [[ -n "$del_id" ]] || return 1
  python3 "$CAL" sign --draft "$del_id" --out "$HOME/.w33-del.json" >/dev/null
  python3 "$CAL" confirm --draft "$del_id" --injection-file "$HOME/.w33-del.json" \
    | sed 's/event=[0-9a-zA-Z_-]\{7,\}/event=<masked>/'
  rm -f "$HOME/.w33-del.json"
}
cleanup_w33_events() {
  local event_id
  while IFS= read -r event_id; do
    [[ -n "$event_id" ]] || continue
    cleanup_event "$event_id" || return 1
  done < <(python3 "$CAL" list --days 3 --query "W3-3" |
    sed -n 's/^EVENT id=\([^ ]*\) .*/\1/p')
}
echo "=== scenario 1/3: happy (live peer, both approvals, gated write) ==="
if ! cleanup_w33_events; then
  echo "W33 FAIL recovery cleanup before happy" >&2
  failed=1
fi
if ! agent "bash \$HOME/.w33_runner.sh happy $range_start $range_end"; then
  cleanup_w33_events || echo "W33 FAIL recovery cleanup after happy" >&2
  failed=1
fi

echo "=== scenario 2/3: refusal (peer declines, 1 renegotiation, 0 writes) ==="
sleep 61
agent "bash \$HOME/.w33_runner.sh refusal $range_start $range_end" || failed=1

echo "=== scenario 3/3: deadlock (peer stopped, escalation DM, 0 writes) ==="
peer_log_lines=0
if ! peer_log_lines="$(peer_gateway_log_lines)"; then
  echo "could not read peer gateway log before restart" >&2
  failed=1
fi
if peer_unit stop; then
  peer_stopped=1
  agent "bash \$HOME/.w33_runner.sh deadlock $range_start $range_end" || failed=1
else
  failed=1
fi
if peer_unit start && wait_for_peer_discord_ready "$peer_log_lines"; then
  peer_stopped=0
else
  failed=1
fi

echo "=== final: both gateways active ==="
for account in agent peer; do
  state="$(ssh "$HOST" "sudo -n -u $account -H bash -lc 'XDG_RUNTIME_DIR=/run/user/\$(id -u) systemctl --user is-active hermes-gateway.service'")"
  echo "gateway $account=$state"
  [[ "$state" == "active" ]] || failed=1
done

if [[ "$failed" -eq 0 ]]; then echo "W3-3-E2E-PASS"; else echo "W3-3-E2E-FAIL"; fi
exit "$failed"
