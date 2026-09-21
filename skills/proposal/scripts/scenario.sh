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
export PROPOSAL_PROFILE=30-page
export KIMM_DOCBOT_PROFILE=30-page
export KNOWLEDGE_FAKE_PACK=1
export PROPOSAL_RESEARCH_TRANSPORT=fake
export PROPOSAL_IMAGE_TRANSPORT=fake
export PROPOSAL_REFINE_TRANSPORT=fake
export DRIVE_TRANSPORT=fake
# style-edit, not identity: refine reports honestly, so a transport that returns its
# input byte-for-byte is NO_CHANGE (refined=false, no refined draft written) — a real
# result, but one that proves nothing about the invariant gates or the output document.
: "${PROPOSAL_REFINE_FAKE_MODE:=style-edit}"
export PROPOSAL_REFINE_FAKE_MODE
mkdir -m 700 -p "$HOME" "$work/bin"

cli=(python3 -I "$script_dir/proposal_cli.py")

# The corpus command calls the in-tree engine in this process, so its converters and
# lint gate run for real here — no shim, and nothing to keep in step with the engine.
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

# draft: retain the public CLI's offline text path, then serialize its machine-consumed
# bundle with the production draft models and sidecar writers. The fake fixture remains
# deterministic while its planspec/pms sidecars keep the real contract.
"${cli[@]}" create --slug demo --title "Offline proposal" --section approach:Approach >/dev/null
"${cli[@]}" draft --slug demo --section approach \
  --text "검증된 근거 20건을 바탕으로 단계별 실증을 수행한다. 이를 통해 목표 성능을 확보한다." >/dev/null
(
  cd "$skill_pkg_root"
  python3 - "$version_dir" <<'PY'
import sys
from pathlib import Path

from proposal.engine.contracts import (
    Claim,
    KPI,
    PlanSpec,
    SectionDraft,
    TraceLink,
    TraceabilityMatrix,
    WorkPackage,
)
from proposal.engine.converter.ingest import ingest_dir
from proposal.engine.converter.materialize import materialize
from proposal.engine.converter.normalize import normalize
from proposal.engine.converter.pms import ProposalMaterialStore
from proposal.engine.pipeline.draft_bundle import save_draft_file, save_planspec
from proposal.engine.pipeline.orchestrator import _save_pms_snapshot
from proposal.scripts.proposal_excavator_e2e import augment
from proposal.scripts.proposal_ir import PROFILES

version_dir = Path(sys.argv[1])
out = version_dir / "out"
raw_docs = ingest_dir(str(version_dir / "corpus"))
units = materialize([normalize(raw) for raw in raw_docs])
planspec = PlanSpec(
    title="Offline proposal",
    trl_start=3,
    trl_end=6,
    objectives=["공개 근거 기반 실증"],
    keywords=["실증"],
    kpis=[KPI("TRL 진전", "단계", "3", "6", 100, "반복 시험", "실증 환경", "공개 근거")],
    work_packages=[WorkPackage("WP1", "단계별 실증", "scenario", 12, ["실증 결과"])],
    page_budget={
        str(section.section_id): section.prose_char_budget
        for section in PROFILES["30-page"].sections
    },
    traceability=TraceabilityMatrix(
        links=[TraceLink("methodology", [unit.unit_id for unit in units])]
    ),
)
drafts = [
    SectionDraft(
        section_id=str(index),
        title=f"Section {index}",
        body="검증된 공개 근거를 바탕으로 단계별 실증을 수행한다. 이를 통해 목표 성능을 확보한다.",
        claims=[Claim(units[0].fact, [units[0].unit_id])],
    )
    for index in range(5)
]
bundle_path = str(out / "drafts.json")
_ = save_draft_file(bundle_path, drafts)
_ = save_planspec(bundle_path, planspec)
_ = _save_pms_snapshot(ProposalMaterialStore(units), f"{bundle_path}.pms.json")
augment(version_dir)
PY
)
printf 'SUBCOMMAND-OK:draft\n'

# images: generate every deterministic slot from the production-shaped draft bundle,
# then prove the second pass uses intact targets/cache by requiring identical output
# and an unchanged spend ledger.
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

# render: the refined bundle must receive the production-shaped sidecars before
# the in-tree engine runs and emits a real HWPX artifact.
render_output="$("${cli[@]}" render --slug demo --mode replay --json 2>&1)"
printf '%s\n' "$render_output"
if grep -Fq 'ENGINE-PIN-BLOCK' <<<"$render_output"; then
  fail "render still consults an external engine pin"
fi
render_json="${render_output##*$'\n'}"
python3 - "$render_json" <<'PY'
import json, pathlib, sys
payload = json.loads(sys.argv[1])
assert payload["profile"] == "30-page"
assert payload["refined"] is True
assert pathlib.Path(payload["hwpx_path"]).is_file()
PY
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
