#!/usr/bin/env bash
# Sandbox scenario for the procurement skill (W1-8 pipeline stage 1 / post-mount smoke).
# Fully offline: builds synthetic docx/xlsx/hwpx/.hwp fixtures in a temp dir, then
# proves preflight detection, .hwp conversion-request with ZERO generation attempts,
# missing-field refusal, register-once/reuse HWPX+DOCX generation, xlsx generation when
# openpyxl is available (else its fail-closed DEPENDENCY-MISSING path),
# and the 25MiB review-DM size branch against stub Discord/gws transports.
set -euo pipefail

fail() { printf 'SCENARIO-FAIL %s\n' "$1" >&2; exit 1; }

secret="${AUTOPHAGY_DEMO_SECRET:-}"
[[ -n "$secret" ]] || fail "AUTOPHAGY_DEMO_SECRET is not set"
[[ "$secret" == DUMMY-* ]] || fail "secret does not carry the DUMMY- prefix (real secrets forbidden in sandbox)"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"
mkdir -p fx out sess stub
export PROCURE_SESSION_DIR="$work/sess" PROCURE_AUDIT_LOG="$work/audit.log" PROCURE_TEMPLATE_DIR="$work/templates"

has_openpyxl=0
python3 -c 'import openpyxl' 2>/dev/null && has_openpyxl=1

cli() { python3 "$script_dir/procure_cli.py" "$@"; }
attempts() { local n; n="$(grep -c GENERATION-ATTEMPT "$PROCURE_AUDIT_LOG" 2>/dev/null)" || true; echo "${n:-0}"; }

# --- 0) synthetic fixtures (stdlib) ------------------------------------------
python3 "$script_dir/make_fixtures.py" fx --large-hwpx-bytes $((26 * 1024 * 1024)) >/dev/null

# --- 1) preflight: three supported formats detected with the pinned parser ----
cli preflight fx/구매요청서-샘플.docx | grep -q 'format=docx parser=python-docx' || fail "docx preflight"
cli preflight fx/지출품의-샘플.xlsx | grep -q 'format=xlsx parser=openpyxl' || fail "xlsx preflight"
cli preflight fx/용역요청서-샘플.hwpx | grep -q 'format=hwpx parser=zip+XML' || fail "hwpx preflight"

# --- 2) binary .hwp → conversion request, ZERO generation ---------------------
set +e
hwp_out="$(cli preflight fx/구양식-샘플.hwp 2>&1)"; hwp_rc=$?
set -e
[[ "$hwp_rc" -eq 3 ]] || fail ".hwp preflight not exit 3 (rc=$hwp_rc)"
grep -q 'CONVERSION-REQUEST' <<<"$hwp_out" || fail ".hwp output lacks CONVERSION-REQUEST"
set +e
cli generate --template fx/구양식-샘플.hwp \
  --fields-json '{"품목":"x","금액":"1000원","업체":"y"}' --out out/nope.hwpx >/dev/null 2>&1
gen_rc=$?
set -e
[[ "$gen_rc" -eq 3 ]] || fail ".hwp generate not refused with exit 3 (rc=$gen_rc)"
[[ "$(attempts)" -eq 0 ]] || fail ".hwp path recorded a generation attempt"
[[ ! -f out/nope.hwpx ]] || fail ".hwp path produced an output file"

# --- 3) conversational collection drives the mapping --------------------------
start="$(cli collect-start --template fx/용역요청서-샘플.hwpx)"
sid="$(sed -n 's/^COLLECT-STARTED session=\([0-9a-f]*\) .*/\1/p' <<<"$start")"
[[ -n "$sid" ]] || fail "no session id"
grep -q 'QUESTION 품목' <<<"$start" || fail "품목 question missing"
cli collect-answer --session "$sid" --field 품목 --value "합성 소모품" >/dev/null
set +e
bad="$(cli collect-answer --session "$sid" --field 금액 --value "많이요" 2>&1)"; bad_rc=$?
set -e
[[ "$bad_rc" -eq 2 ]] || fail "non-numeric amount accepted (rc=$bad_rc)"
grep -q 'ANSWER-REJECTED' <<<"$bad" || fail "amount rejection marker missing"

# --- 4) missing required field → refuse + list, no file -----------------------
set +e
refuse="$(cli generate --session "$sid" --out out/refused.hwpx 2>&1)"; refuse_rc=$?
set -e
[[ "$refuse_rc" -eq 5 ]] || fail "missing-field generate not exit 5 (rc=$refuse_rc)"
grep -q 'GENERATION-REFUSED' <<<"$refuse" || fail "refusal marker missing"
grep -q -- '- 금액' <<<"$refuse" && grep -q -- '- 업체' <<<"$refuse" || fail "missing-field list incomplete"
[[ ! -f out/refused.hwpx ]] || fail "refused generate still wrote a file"

# --- 5) complete collection → hwpx generate + zip+XML verify (stdlib) ---------
cli collect-answer --session "$sid" --field 금액 --value "123,000원" >/dev/null
cli collect-answer --session "$sid" --field 업체 --value "합성벤더" | grep -q COLLECT-COMPLETE \
  || fail "collection did not complete"
cli generate --session "$sid" --out out/draft.hwpx | grep -q 'VERIFIED parser=zip+XML' \
  || fail "hwpx generate/verify failed"

# --- 6) register once → reuse stored HWPX and DOCX templates -----------------
cli register --name po_form --template fx/빈슬롯-구매요청서-샘플.hwpx | grep -q 'REGISTERED name=po_form format=hwpx' \
  || fail "hwpx register"
cli templates-show --name po_form | grep -q '"품목"' || fail "hwpx template show"
cli generate --template-name po_form \
  --fields-json '{"품목":"합성 소모품","금액":"123,000원","업체":"합성벤더"}' \
  --out out/registered.hwpx | grep -q 'VERIFIED parser=zip+XML' || fail "registered hwpx generate/verify"
cli register --name po_doc --template fx/구매요청서-샘플.docx | grep -q 'REGISTERED name=po_doc format=docx' \
  || fail "docx register"
cli generate --template-name po_doc \
  --fields-json '{"품목":"합성 소모품","금액":"123,000원","업체":"합성벤더"}' \
  --out out/registered.docx | grep -q 'VERIFIED parser=python-docx' || fail "registered docx generate/verify"
python3 - out/registered.hwpx out/registered.docx <<'PY' || fail "registered output content"
import sys
import zipfile

for path in sys.argv[1:]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        member = "Contents/section0.xml" if path.endswith(".hwpx") else "word/document.xml"
        text = archive.read(member).decode("utf-8")
    assert "{{" not in text, path
    assert "합성 소모품" in text and "123,000원" in text and "합성벤더" in text and "<hp:t/>" not in text, path
PY

# --- 7) xlsx leg: full when library exists, fail-closed otherwise --------------
leg="hwpx+docx+failclosed-xlsx"
if [[ "$has_openpyxl" -eq 1 ]]; then
  cli generate --template fx/지출품의-샘플.xlsx \
    --fields-json '{"품목":"합성 소모품","금액":"123,000원","업체":"합성벤더"}' \
    --out out/draft.xlsx | grep -q 'VERIFIED parser=openpyxl' || fail "xlsx generate/verify"
  leg="full-3format-registry"
else
  set +e
  dep_out="$(cli generate --template fx/지출품의-샘플.xlsx \
    --fields-json '{"품목":"x","금액":"1000원","업체":"y"}' --out out/dep.xlsx 2>&1)"
  dep_rc=$?
  set -e
  [[ "$dep_rc" -eq 7 ]] || fail "xlsx without lib not exit 7 (rc=$dep_rc)"
  grep -q 'DEPENDENCY-MISSING' <<<"$dep_out" || fail "dependency marker missing"
  [[ ! -f out/dep.xlsx ]] || fail "dependency-missing path left a partial file"
fi

# --- 8) review DM size branch against stubs (no network) ----------------------
cat > gws-stub <<'SH'
#!/bin/sh
printf '%s\n' "$*" >> "$(dirname "$0")/gws-calls.log"
case "$*" in
  *"files list"*) printf '{"files":[]}\n' ;;
  *"files create"*) printf '{"id":"stub-fold"}\n' ;;
  *"+upload"*) printf '{"id":"stub-drive-1"}\n' ;;
  *"files get"*) printf '{"webViewLink":"https://drive.google.com/file/d/stub-drive-1/view"}\n' ;;
esac
SH
chmod +x gws-stub
cli generate --template "fx/대형-용역요청서-샘플.hwpx" \
  --fields-json '{"품목":"대형 테스트","금액":"999,000원","업체":"합성벤더"}' \
  --out out/large.hwpx | grep -q VERIFIED || fail "large hwpx generate"
small_line="$(PROCURE_DISCORD_STUB="$work/stub" PROCURE_GWS_BIN="$work/gws-stub" \
  cli review --file out/draft.hwpx --note "합성")"
grep -q 'mode=attach' <<<"$small_line" || fail "small file did not take the attach branch"
large_line="$(PROCURE_DISCORD_STUB="$work/stub" PROCURE_GWS_BIN="$work/gws-stub" \
  cli review --file out/large.hwpx --note "합성 대형")"
grep -q 'mode=drive-link' <<<"$large_line" || fail "26MiB file did not take the drive-link branch"
grep -q 'drive +upload' gws-calls.log || fail "drive upload argv missing from stub log"

printf 'SCENARIO-PASS leg=%s secret_len=%s account=%s\n' "$leg" "${#secret}" "$(whoami)"
