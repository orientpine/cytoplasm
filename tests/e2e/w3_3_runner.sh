#!/usr/bin/env bash
# W3-3 per-scenario E2E runner — executes ON the agent account (<primary-node>).
# usage: w3_3_runner.sh <happy|deadlock|refusal> <range_start> <range_end>
# Requires: ~/.w33-e2e.secret (per-run hex), ~/.w33_probe.py (pushed by the
# orchestrator tests/e2e/w3_3_coordination.sh). Prints masked observables only.
set -euo pipefail
scenario="$1"; range_start="$2"; range_end="$3"
set -a; . "$HOME/.env.secrets"; set +a
export PYTHONPATH="$HOME/.hermes/interop_runtime"
export E2E_TEST_MODE=1
INTEROP_E2E_SECRET="$(cat "$HOME/.w33-e2e.secret")"
export INTEROP_E2E_SECRET
CLI="/srv/autophagy-skills/live/coordination/scripts/coordinate_cli.py"
CAL="/srv/autophagy-skills/live/calendar/scripts/calendar_cli.py"
PROBE="$HOME/.w33_probe.py"
APPR=/srv/autophagy-agents/logs/approvals.jsonl
SUMMARY="W3-3 조율 테스트 미팅"

appr() { wc -l < "$APPR"; }
evt() { python3 "$CAL" list --days 3 --query "W3-3" | { grep -c '^EVENT' || true; }; }
say() { printf 'W33 %s\n' "$*"; }
corr_of() { sed -n 's/.*correlation=\(coord-[0-9a-f]*\).*/\1/p' <<<"$1" | head -1; }

a0="$(appr)"; e0="$(evt)"
say "T0 scenario=$scenario utc=$(date -u +%FT%TZ) approvals=$a0 events=$e0"
common=(request --peer peer-test --summary "$SUMMARY"
        --range-start "$range_start" --range-end "$range_end" --duration-min 30)

case "$scenario" in
happy)
  t_start="$(date -u +%FT%TZ)"
  set +e; out="$(python3 "$CLI" "${common[@]}" --timeout-s 60 --e2e-confirm 2>&1)"; rc=$?; set -e
  printf '%s\n' "$out" | sed 's/event=[0-9a-zA-Z_-]\{7,\}/event=<masked>/'
  [[ $rc -eq 0 ]] || { say "FAIL happy rc=$rc (want 0)"; exit 1; }
  corr="$(corr_of "$out")"
  a1="$(appr)"; e1="$(evt)"
  say "post-run approvals_delta=$((a1 - a0)) events=$e1 (want +2, $((e0 + 1)))"
  [[ $((a1 - a0)) -eq 2 && "$e1" -eq $((e0 + 1)) ]] || { say "FAIL write-proof"; exit 1; }
  python3 "$PROBE" dm-check "$corr" "[E2E]" "일정 조율 완료" || { say "FAIL result-dm"; exit 1; }
  say "cascade-monitor 120s (watching #team after $t_start)"
  sleep 120
  monitor="$(python3 "$PROBE" team-after "$t_start")"
  printf '%s\n' "$monitor"
  cascade_ok=1
  grep -q "notices=1 others=0" <<<"$monitor" || cascade_ok=0
  a2="$(appr)"
  [[ "$a2" -eq "$a1" ]] || cascade_ok=0
  [[ "$cascade_ok" -eq 1 ]] && say "cascade-safe: terse notice only, approvals_delta_after_monitor=0"
  event_id="$(python3 "$CAL" list --days 3 --query "W3-3" | sed -n 's/^EVENT id=\([^ ]*\) .*/\1/p' | head -1)"
  [[ -n "$event_id" ]] || { say "FAIL cleanup: event not found"; exit 1; }
  del_out="$(python3 "$CAL" draft-delete --event-id "$event_id" --label "$SUMMARY")"
  del_id="$(sed -n 's/^DRAFT-CREATED id=\([0-9a-f]*\) .*/\1/p' <<<"$del_out")"
  python3 "$CAL" sign --draft "$del_id" --out "$HOME/.w33-del.json" >/dev/null
  python3 "$CAL" confirm --draft "$del_id" --injection-file "$HOME/.w33-del.json" \
    | sed 's/event=[0-9a-zA-Z_-]\{7,\}/event=<masked>/'
  rm -f "$HOME/.w33-del.json"
  e3="$(evt)"; a3="$(appr)"
  say "cleanup events=$e3 (want $e0) approvals_total_delta=$((a3 - a0))"
  [[ "$e3" -eq "$e0" ]] || { say "FAIL cleanup: event still present"; exit 1; }
  [[ "$cascade_ok" -eq 1 ]] || { say "FAIL cascade-detected (cleanup done)"; exit 1; }
  say "HAPPY-PASS corr=$corr"
  ;;
deadlock)
  set +e; out="$(python3 "$CLI" "${common[@]}" --timeout-s 15 2>&1)"; rc=$?; set -e
  printf '%s\n' "$out"
  [[ $rc -eq 4 ]] || { say "FAIL deadlock rc=$rc (want 4)"; exit 1; }
  corr="$(corr_of "$out")"
  a1="$(appr)"; e1="$(evt)"
  say "post-run approvals_delta=$((a1 - a0)) events=$e1 (want 0, $e0)"
  [[ $((a1 - a0)) -eq 0 && "$e1" -eq "$e0" ]] || { say "FAIL zero-write proof"; exit 1; }
  python3 "$PROBE" dm-check "$corr" "[E2E]" "에스컬레이션" "인간 협의" || { say "FAIL escalation-dm"; exit 1; }
  say "DEADLOCK-PASS corr=$corr escalation_dm=found calendar_writes=0"
  ;;
refusal)
  set +e; out="$(python3 "$CLI" "${common[@]}" --timeout-s 60 --e2e-confirm --peer-decline 2>&1)"; rc=$?; set -e
  printf '%s\n' "$out"
  [[ $rc -eq 5 ]] || { say "FAIL refusal rc=$rc (want 5)"; exit 1; }
  corr="$(corr_of "$out")"
  declines="$(grep -c '"accepted": false' <<<"$out" || true)"
  [[ "$declines" -eq 2 ]] || { say "FAIL renegotiation count declines=$declines (want 2)"; exit 1; }
  a1="$(appr)"; e1="$(evt)"
  say "post-run approvals_delta=$((a1 - a0)) events=$e1 (want 0, $e0)"
  [[ $((a1 - a0)) -eq 0 && "$e1" -eq "$e0" ]] || { say "FAIL zero-write proof"; exit 1; }
  python3 "$PROBE" dm-check "$corr" "[E2E]" "일정 조율 종료" || { say "FAIL termination-dm"; exit 1; }
  say "REFUSAL-PASS corr=$corr renegotiations=1 calendar_writes=0"
  ;;
*)
  say "usage: w3_3_runner.sh <happy|deadlock|refusal> <range_start> <range_end>"; exit 2 ;;
esac
