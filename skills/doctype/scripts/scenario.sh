#!/usr/bin/env bash
# Fully offline E5 smoke: local stubs exercise the real Codex routing path.
set -euo pipefail

fail() { printf 'SCENARIO-FAIL %s\n' "$1" >&2; exit 1; }

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../../.." && pwd)"
work="$(mktemp -d)"
trap 'cd / && rm -rf "$work"' EXIT

export PYTHONPATH="$repo_root"
export DOCTYPE_REPO_ROOT="$work/metadata-repo"
export DOCTYPE_OVERLAY_ROOT="$work/overlay"
export DOCTYPE_PRIVATE_ROOT="$work/private"
export DOCTYPE_LLM_LOG="$work/logs/llm-calls.jsonl"
export DOCTYPE_RULES_FILE="$script_dir/../configs/sensitivity-rules.yaml"
mkdir -p "$work/metadata-repo"

cat > "$work/glm-stub" <<'PY'
#!/usr/bin/env python3
from pathlib import Path
Path(__file__).with_name("glm-called").write_text("unexpected\n", encoding="utf-8")
raise SystemExit(91)
PY
cat > "$work/hermes-stub" <<'PY'
#!/usr/bin/env python3
import sys
from pathlib import Path

prompt = sys.argv[2] if len(sys.argv) > 2 else ""
base = Path(__file__).resolve().parent
stage = "extract" if "DOCTYPE_STAGE=EXTRACT" in prompt else "narrative"
with (base / "codex-calls.log").open("a", encoding="utf-8") as handle:
    handle.write(stage + "\n")
if stage == "extract":
    print('{"gist":"목적과 근거를 순서대로 제시하는 서류",'
          '"tone":"공식적이고 검증 가능한 표현", "mode":"narrative",'
          '"sections":[{"title":"추천 대상","guidance":"대상을 사실로 식별한다.","kind":"slot-fill"},'
          '{"title":"추천 사유","guidance":"입력 사실을 연결해 추천 논리를 작성한다.","kind":"narrative"},'
          '{"title":"선정 근거","guidance":"역량과 일정 근거를 종합한다.","kind":"narrative"}]}')
else:
    print("제공된 사실을 바탕으로 해당 업체는 과업 이해와 수행 역량을 갖추었으며, 일정 대응 가능성을 함께 고려하여 추천합니다.")
PY
chmod +x "$work/glm-stub" "$work/hermes-stub"
export DOCTYPE_GLM_BIN="$work/glm-stub"
export DOCTYPE_HERMES_BIN="$work/hermes-stub"

python3 "$script_dir/make_fixtures.py" --out "$work/examples" > "$work/fixtures.out" || fail "fixture generation"
cat > "$work/inputs.json" <<'JSON'
{"업체명":"합성 수행사","사업명":"합성 과업"}
JSON

cli() { python3 "$script_dir/doctype_cli.py" "$@"; }

cli register-from-example --name "업체추천사유서" --example "$work/examples/synthetic-vendor-reason.md" --mode narrative > "$work/register.out" || fail "register"
grep -q 'REGISTERED .*version=1 .*mode=narrative' "$work/register.out" || fail "register output"
cli show --name "업체추천사유서" > "$work/show-v1.json" || fail "show"
python3 - "$work/show-v1.json" <<'PY' || fail "show metadata contract"
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["version"] == 1 and value["mode"] == "narrative"
assert "body" not in value and value["examples"][0]["ref"].startswith("private:")
PY
cli draft --name "업체추천사유서" --inputs-json "$work/inputs.json" --out "$work/drafts/vendor.md" > "$work/draft.out" || fail "draft"
grep -q 'DRAFTED .*narrative_sections=section-02,section-03' "$work/draft.out" || fail "narrative section routing"
grep -q '수행 역량을 갖추었으며' "$work/drafts/vendor.md" || fail "stub Codex prose missing"
cli refine --name "업체추천사유서" --approved "$work/drafts/vendor.md" --note "승인본을 few-shot으로 추가" > "$work/refine.out" || fail "refine"
grep -q 'REFINED .*version=2 .*examples=2' "$work/refine.out" || fail "version did not bump"

canary="SENSITIVE-CANARY-$(python3 -c 'import secrets; print(secrets.token_hex(6))')"
printf '# 민감 예시\n\n특허 출원 검토 %s\n' "$canary" > "$work/sensitive.md"
cli register-from-example --name "민감서류" --example "$work/sensitive.md" > "$work/sensitive.out" || fail "sensitive reroute"
grep -q 'sensitivity=patent-sensitive' "$work/sensitive.out" || fail "sensitivity metadata"
python3 - "$repo_root" <<'PY' || fail "sensitive GLM gate"
import sys
sys.path.insert(0, sys.argv[1])
from skills.doctype.scripts import doctype_llm
try:
    doctype_llm.call_glm("never send", sensitive=True)
except doctype_llm.PatentRoutingError:
    pass
else:
    raise AssertionError("sensitive GLM call was not refused")
PY

[[ ! -e "$work/glm-called" ]] || fail "GLM stub was called"
grep -q "$canary" "$work/logs/llm-calls.jsonl" && fail "document body leaked to audit log"
grep -R -q "$canary" "$work/metadata-repo" "$work/overlay" && fail "document body leaked to metadata"
python3 - "$work/logs/llm-calls.jsonl" <<'PY' || fail "masked audit log contract"
import json, sys
records = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
assert records and all(set(item) == {"model", "opaque_id", "provider", "purpose", "sensitive", "timestamp"} for item in records)
assert all(item["provider"] == "openai-codex" for item in records)
assert any(item["sensitive"] for item in records)
PY

grep '^REGISTERED ' "$work/register.out"
echo "SHOW version=1 metadata_body_key=false"
grep '^DRAFTED ' "$work/draft.out"
grep '^REFINED ' "$work/refine.out"
echo "SCENARIO-PASS doctype offline (register/extract/narrative/refine/v2/sensitive-glm-0/masked-log)"
