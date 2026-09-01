#!/usr/bin/env bash
# Fully offline v2 proposal pipeline scenario. Every external transport is fake and
# the unavailable KD checkout is represented by a workspace-local converter/lint shim.
set -euo pipefail

fail() { printf 'SCENARIO-FAIL %s\n' "$1" >&2; exit 1; }

secret="${AUTOPHAGY_DEMO_SECRET:-}"
[[ -n "$secret" ]] || fail "AUTOPHAGY_DEMO_SECRET is not set"
[[ "$secret" == DUMMY-* ]] || fail "secret does not carry the DUMMY- prefix (real secrets forbidden)"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The directory that holds the `proposal` package: skills/ in a checkout, ~/.hermes/skills/
# in the deploy sandbox, live/ once mounted. AUTOPHAGY_REPO_ROOT points at the release
# tree, where the package lives under skills/, so it must not steer this lookup.
skill_pkg_root="$(cd "$script_dir/../.." && pwd)"
work="$(mktemp -d)"
chmod 700 "$work"
trap 'rm -rf "$work"' EXIT

export HOME="$work/home"
export PROPOSAL_ROOT="$work/proposals"
export PROPOSAL_WORKSPACE_ROOT="$work/workspace"
export PROPOSAL_STATUS_ROOT="$work/status"
export PROPOSAL_STATE_ROOT="$work/state"
export PROPOSAL_KANBAN_DISABLED=1
export PROPOSAL_DM_DISABLED=1
export KNOWLEDGE_FAKE_PACK=1
export PROPOSAL_RESEARCH_TRANSPORT=fake
export PROPOSAL_IMAGE_TRANSPORT=fake
export PROPOSAL_REFINE_TRANSPORT=fake
export DRIVE_TRANSPORT=fake
export PROPOSAL_DOCBOT_ROOT="$work/docbot"
export PROPOSAL_DOCBOT_PIN=0000000000000000000000000000000000000000
# style-edit, not identity: refine reports honestly, so a transport that returns its
# input byte-for-byte is NO_CHANGE (refined=false, no refined draft written) — a real
# result, but one that proves nothing about the invariant gates or the output document.
: "${PROPOSAL_REFINE_FAKE_MODE:=style-edit}"
export PROPOSAL_REFINE_FAKE_MODE
mkdir -m 700 -p "$HOME" "$PROPOSAL_DOCBOT_ROOT" "$work/bin"

cli=(python3 -I "$script_dir/proposal_cli.py")

# The corpus command has no fake-runner environment seam. This tiny uv shim proves
# both converter invocation and the lint gate without requiring KD or a network.
cat >"$work/bin/uv" <<'SH'
#!/bin/sh
set -eu
case "$*" in
  *" kimm-docbot research-convert "*)
    out=""
    while [ "$#" -gt 0 ]; do
      if [ "$1" = "--out" ]; then out="$2"; break; fi
      shift
    done
    [ -n "$out" ]
    mkdir -p "$out"
    printf '%s\n' '---' 'sensitivity: public' '---' 'Offline validated research corpus.' >"$out/research-scenario.md"
    ;;
  *" kimm-docbot corpus-lint "*) exit 0 ;;
  *) printf 'unexpected fake uv invocation: %s\n' "$*" >&2; exit 64 ;;
esac
SH
chmod 700 "$work/bin/uv"
export PATH="$work/bin:${PATH:-/usr/bin:/bin}"

# research: fake collection plus the production validator (20 claims, 8 domains,
# and all five section-coverage gates).
research_json="$("${cli[@]}" research --slug demo --goal "offline proposal validation" --json)"
python3 - "$research_json" <<'PY'
import json, pathlib, sys
payload = json.loads(sys.argv[1])
assert payload["slug"] == "demo"
assert pathlib.Path(payload["brief"]).is_file()
assert pathlib.Path(payload["synthesis"]).is_file()
PY
"${cli[@]}" research --slug demo --validate-only --json >/dev/null
printf 'SUBCOMMAND-OK:research\n'

# corpus: run the real subcommand through the local converter and lint shim.
corpus_json="$("${cli[@]}" corpus --slug demo --json)"
python3 - "$corpus_json" <<'PY'
import json, pathlib, sys
payload = json.loads(sys.argv[1])
files = [pathlib.Path(item) for item in payload["files"]]
assert payload["slug"] == "demo" and files
assert all(path.is_file() and path.name.endswith(".md") for path in files)
PY
printf 'SUBCOMMAND-OK:corpus\n'

version="$(cat "$PROPOSAL_ROOT/demo/HEAD")"
version_dir="$PROPOSAL_ROOT/demo/versions/$version"

# Seed the deterministic 15-slot figure IR expected from the offline drafting leg.
python3 - "$version_dir" <<'PY'
import json, os, pathlib, sys
root = pathlib.Path(sys.argv[1])
figures = [
    {
        "figure_id": f"fig-s1-{index:02d}",
        "section_id": "s1",
        "source_claim_ids": [f"public:C{index:02d}"],
        "prompt": f"public construction robotics concept slot-{index}",
        "caption": f"Validated figure {index}",
        "png_sha256": "",
        "band_index": index - 1,
    }
    for index in range(1, 16)
]
path = root / "figures.json"
path.write_text(json.dumps(figures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
os.chmod(path, 0o600)
PY

# images: generate all slots, then prove the second pass uses intact targets/cache
# by requiring identical output and an unchanged spend ledger.
images_first="$("${cli[@]}" images --slug demo --json)"
ledger_before="$(python3 - "$PROPOSAL_STATE_ROOT/image_spend.json" <<'PY'
import hashlib, pathlib, sys
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
images_second="$("${cli[@]}" images --slug demo --json)"
ledger_after="$(python3 - "$PROPOSAL_STATE_ROOT/image_spend.json" <<'PY'
import hashlib, pathlib, sys
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
[[ "$images_first" == "$images_second" ]] || fail "image cache replay changed the result"
[[ "$ledger_before" == "$ledger_after" ]] || fail "image cache replay charged the ledger"
python3 - "$images_first" <<'PY'
import json, pathlib, sys
payload = json.loads(sys.argv[1])
assert payload["missing"] == [] and len(payload["images"]) == 15
assert all(pathlib.Path(item["path"]).is_file() for item in payload["images"])
PY
printf 'SUBCOMMAND-OK:images\n'

# draft: exercise the public CLI's offline text path (no KD checkout), then write
# the corresponding machine-consumed draft bundle for refine/render.
"${cli[@]}" create --slug demo --title "Offline proposal" --section approach:Approach >/dev/null
"${cli[@]}" draft --slug demo --section approach \
  --text "검증된 근거 20건을 바탕으로 단계별 실증을 수행한다. 이를 통해 목표 성능을 확보한다." >/dev/null
python3 - "$version_dir" <<'PY'
import json, os, pathlib, sys
out = pathlib.Path(sys.argv[1]) / "out"
out.mkdir(mode=0o700, exist_ok=True)
payload = {
    "sections": [{
        "section_id": "s1",
        "title": "Approach",
        "body": "검증된 근거 20건을 바탕으로 단계별 실증을 수행한다. 이를 통해 목표 성능을 확보한다. [[FIG:fig-s1-01]]",
    }]
}
path = out / "drafts.json"
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
os.chmod(path, 0o600)
PY
printf 'SUBCOMMAND-OK:draft\n'

# refine must precede render. A broken fake mode returns non-zero, and set -e
# propagates that failure rather than printing any later success marker. PASS (not
# NO_CHANGE) is the assertion because the draft above carries the phrase style-edit
# rewrites: the refined document must actually be produced and pass the gates.
refine_json="$("${cli[@]}" refine --slug demo --json)"
python3 - "$refine_json" <<'PY'
import json, pathlib, sys
payload = json.loads(sys.argv[1])
assert payload["refined"] is True
assert payload["invariants"] == "PASS"
assert payload["failed_chunks"] == 0
assert pathlib.Path(payload["path"]).is_file()
PY
printf 'SUBCOMMAND-OK:refine\n'

# render: in this sandbox the KD engine is intentionally unavailable. The exact
# fail-closed preflight (exit 4 plus marker) is the render-stage proof.
set +e
render_output="$("${cli[@]}" render --slug demo --mode replay --json 2>&1)"
render_rc=$?
set -e
[[ "$render_rc" -eq 4 ]] || fail "render preflight did not exit 4 (rc=$render_rc)"
grep -Fq 'ENGINE-PIN-BLOCK:' <<<"$render_output" || fail "render preflight block marker missing"
printf '%s\n' "$render_output"
printf 'SUBCOMMAND-OK:render\n'

# publish: fake Drive still executes folder creation, upload, owner-only permission
# verification, SHA read-back, manifest finalization, and receipt-last. A replay
# must perform zero uploads.
publish_first="$("${cli[@]}" publish --slug demo --version "$version" --json)"
publish_second="$("${cli[@]}" publish --slug demo --version "$version" --json)"
python3 - "$publish_first" "$publish_second" <<'PY'
import json, pathlib, sys
first, second = map(json.loads, sys.argv[1:])
assert first["slug"] == "demo" and first["uploads"]
assert pathlib.Path(first["receipt"]).is_file()
assert second["uploads"] == []
assert len(first["files"]) >= 15
assert all(item["delivery"] == "drive-link" and item["id"] for item in first["files"])
PY
printf 'SUBCOMMAND-OK:publish\n'

# version: create the same request twice through the version implementation. The
# first promotes a child and the second must report reused:true for that child.
version_first="$(cd "$skill_pkg_root" && python3 -m proposal.scripts.proposal_version \
  create --slug demo --directive scenario=v2 --json)"
version_second="$(cd "$skill_pkg_root" && python3 -m proposal.scripts.proposal_version \
  create --slug demo --directive scenario=v2 --json)"
"${cli[@]}" version --slug demo --json >/dev/null
python3 - "$version_first" "$version_second" <<'PY'
import json, sys
first, second = map(json.loads, sys.argv[1:])
assert first["reused"] is False
assert second["reused"] is True
assert first["version"] == second["version"] == second["head"]
PY
printf 'SUBCOMMAND-OK:version\n'
printf 'SCENARIO-PASS proposal v2 offline pipeline\n'
