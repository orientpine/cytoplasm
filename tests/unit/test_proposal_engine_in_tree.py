"""Private-engine packaging and credential-free local rendering contracts."""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Final

import pytest
from tests.unit.test_proposal_ir import mounted_proposal as mounted_proposal

ENGINE_ROOT: Final = Path(__file__).resolve().parents[2] / "skills" / "proposal" / "engine"


ENGINE_MODULE: Final = "skills.proposal.engine"


ALLOWED_FIRST_PARTY: Final = frozenset({"skills", "automation", "__future__"})


def _engine_sources() -> list[Path]:
    return sorted(ENGINE_ROOT.rglob("*.py"))


def _imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_bytes(), filename=str(path))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            names = [node.module or ""]
        else:
            continue
        roots.update(name.split(".")[0] for name in names if name)
    return roots


def test_the_engine_lives_in_this_repository() -> None:
    assert ENGINE_ROOT.is_dir(), f"in-tree render engine missing at {ENGINE_ROOT}"
    assert _engine_sources(), "engine directory carries no python sources"


def test_mounted_engine_reexports_its_own_public_profile(mounted_proposal: Path) -> None:
    # Given: the engine and public profile share one mounted proposal package.
    # When: the private compatibility module is imported through that package.
    result = subprocess.run(
        [sys.executable, "-I", "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); "
         + "from proposal import layout_profile as public; "
         + "from proposal.engine.contracts import layout_profile as private; "
         + "assert private.LAYOUT_PROFILES is public.LAYOUT_PROFILES",
         str(mounted_proposal.parent)],
        cwd=mounted_proposal.parent, capture_output=True, text=True,
        timeout=30, check=False,
    )
    # Then: no checkout-qualified import or duplicated profile is required.
    assert result.returncode == 0, result.stderr


def test_the_engine_ships_the_form_it_renders_into() -> None:
    seeds = sorted(ENGINE_ROOT.rglob("*.hwpx"))

    assert seeds, "engine ships no seed .hwpx; render would depend on an outside checkout"


def test_the_engine_imports_no_third_party_package() -> None:
    sources = _engine_sources()
    assert sources, "no engine sources to inspect; the purity claim would be vacuous"

    stdlib = set(sys.stdlib_module_names)
    offenders: dict[str, list[str]] = {}
    for source in sources:
        outside = {
            root
            for root in _imported_roots(source)
            if root not in stdlib and root not in ALLOWED_FIRST_PARTY
        }
        if outside:
            offenders[str(source.relative_to(ENGINE_ROOT))] = sorted(outside)

    assert not offenders, f"engine reaches outside the standard library: {offenders}"


def test_engine_digest_changes_when_shared_profile_source_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an engine tree whose layout definition lives in the public sibling.
    from skills.proposal.engine import render
    engine = tmp_path / "proposal" / "engine"
    engine.mkdir(parents=True)
    _ = (engine / "render.py").write_bytes((ENGINE_ROOT / "render.py").read_bytes())
    shared = engine.parent / "layout_profile.py"
    original = (ENGINE_ROOT.parent / "layout_profile.py").read_text(encoding="utf-8")
    _ = shared.write_text(original, encoding="utf-8")
    monkeypatch.setattr(render, "ENGINE_ROOT", engine)
    before = render.engine_digest()

    # When: the shared profile changes but the engine's own files do not.
    _ = shared.write_text(original.replace("max_chars=15_000", "max_chars=15_001"), encoding="utf-8")

    # Then: artifact provenance distinguishes the two rendering contracts.
    assert render.engine_digest() != before


@pytest.fixture
def local_render_bundle(tmp_path: Path) -> Path:
    """Build genuine drafts, corpus, figures and sidecars without an LLM."""
    from skills.proposal.engine.contracts.models import (
        KPI, PlanSpec, SectionDraft, TraceabilityMatrix, WorkPackage,
    )
    from skills.proposal.engine.pipeline.draft_bundle import save_draft_file, save_planspec
    from skills.proposal.layout_profile import get_layout_profile
    from skills.proposal.scripts.proposal_images import fake_png
    from skills.proposal.scripts.proposal_ir import FigureSpec, figures_to_json
    from skills.proposal.scripts.proposal_version import Staging, VersionStore

    store = VersionStore(tmp_path / "proposals")
    staging = store.begin("local-render", "a" * 64)
    assert isinstance(staging, Staging)
    version = store.promote("local-render", staging, {"schema_version": 1, "parent": None})
    directory = store.resolve_slug_dir("local-render") / "versions" / version
    _ = (directory / "corpus" / "public.txt").write_text(
        "공개 연구 결과는 반복 시험으로 성능을 검증한다.", encoding="utf-8",
    )
    profile = get_layout_profile("10-page")
    plan = PlanSpec(
        title="공개 성과 활용 계획", trl_start=3, trl_end=6,
        objectives=["공개 성과 검증"], keywords=["검증"],
        kpis=[KPI("성능 검증", "%", "80", "95", 100, "반복 시험", "시험실", "공개 근거")],
        work_packages=[WorkPackage("WP1", "성과 검증", "연구팀", 12, ["시험 보고서"])],
        page_budget={str(key): value for key, value in profile.prose_budgets.items()},
        traceability=TraceabilityMatrix(links=[]),
    )
    figures: list[FigureSpec] = []
    drafts: list[SectionDraft] = []
    for section, count in profile.figure_targets.items():
        paragraphs: list[str] = []
        for slot in range(count):
            identifier = f"fig-s{section}-{slot:02d}"
            png = fake_png(identifier)
            _ = (directory / "images" / f"{identifier}.png").write_bytes(png)
            figures.append(FigureSpec(
                identifier, str(section), (), "offline fixture", "공개 성과 검증 절차",
                hashlib.sha256(png).hexdigest(), len(figures),
            ))
            paragraphs.append(
                f"[[FIG:{identifier}]] 기술이전 계획은 공개 성과의 활용 절차를 설명한다. "
                + "반복 시험으로 성능을 확인하고 검증 결과를 바탕으로 후속 활용을 추진한다."
            )
        drafts.append(SectionDraft(str(section), f"Section {section}", "\n\n".join(paragraphs), []))
    records: list[dict[str, object]] = json.loads(figures_to_json(figures))
    for record in records:
        record["model"] = "offline-fixture"
    _ = (directory / "figures.json").write_text(json.dumps(records), encoding="utf-8")
    _ = (directory / "tables.json").write_text("[]", encoding="utf-8")
    bundle = str(directory / "out" / "drafts.json")
    _ = save_draft_file(bundle, drafts)
    _ = save_planspec(bundle, plan)
    _ = (directory / "out" / "refine-report.json").write_text(
        json.dumps({"refined": False, "reason": "local-only-fixture"}), encoding="utf-8",
    )
    return directory


def test_technical_transfer_body_renders_when_credentials_and_network_are_absent(
    local_render_bundle: Path, tmp_path: Path,
) -> None:
    # Given: real on-disk inputs and a fresh interpreter with no credentials.
    home = tmp_path / "home"
    home.mkdir()
    code = """
import sys
from unittest.mock import patch
with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')), \\
     patch('socket.getaddrinfo', side_effect=AssertionError('DNS forbidden')), \\
     patch('subprocess.Popen', side_effect=AssertionError('subprocess forbidden')):
    from skills.proposal.scripts.proposal_render import run_render
    result = run_render('local-render', profile='10-page')
    assert not any(name.endswith('.hermes_client') for name in sys.modules)
    print(result.hwpx_path)
"""
    # When: the actual skill boundary invokes the actual in-tree HWPX renderer.
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ENGINE_ROOT.parents[2],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin",
             "PROPOSAL_ROOT": str(tmp_path / "proposals")},
        capture_output=True, text=True, timeout=30, check=False,
    )
    # Then: technical-transfer source text reaches the real artifact without a model/client.
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(local_render_bundle / "out" / "proposal.hwpx") as archive:
        assert "기술이전" in archive.read("Preview/PrvText.txt").decode("utf-8")


def test_mounted_cli_renders_with_its_own_engine_when_checkout_is_absent(
    local_render_bundle: Path, mounted_proposal: Path, tmp_path: Path,
) -> None:
    # Given: real drafts and figures, a mounted skill, and no credentials or checkout.
    home = tmp_path / "mounted-home"
    home.mkdir()
    code = """
import runpy
import sys
from pathlib import Path
from unittest.mock import patch
script = Path(sys.argv[1])
sys.argv = [str(script), 'render', '--slug', 'local-render', '--profile', '10-page', '--json']
with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')), \\
     patch('socket.getaddrinfo', side_effect=AssertionError('DNS forbidden')), \\
     patch('subprocess.Popen', side_effect=AssertionError('subprocess forbidden')):
    try:
        runpy.run_path(str(script), run_name='__main__')
    except SystemExit as result:
        assert result.code == 0
    from proposal.engine import render
    assert render.ENGINE_ROOT == script.resolve().parents[1] / 'engine'
    assert not any(name == 'skills' or name.startswith('skills.') for name in sys.modules)
    assert not any(name.endswith('.hermes_client') for name in sys.modules)
"""
    # When: the actual mounted file CLI performs the full in-process render.
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(mounted_proposal / "scripts/proposal_cli.py")],
        cwd=mounted_proposal.parent,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin",
             "PROPOSAL_ROOT": str(tmp_path / "proposals")},
        capture_output=True, text=True, timeout=30, check=False,
    )
    # Then: a real HWPX contains the fixture body and its manifest binds its bytes.
    assert result.returncode == 0, result.stdout + result.stderr
    artifact = local_render_bundle / "out" / "proposal.hwpx"
    with zipfile.ZipFile(artifact) as archive:
        assert "기술이전" in archive.read("Preview/PrvText.txt").decode("utf-8")
    manifest_json = (local_render_bundle / "manifest.json").read_text()
    assert json.loads(manifest_json)["hwpx_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert json.loads(manifest_json)["profile"] == "10-page"


def test_mounted_engine_modules_import_without_checkout(mounted_proposal: Path) -> None:
    # Given: only the mounted package is added to an isolated interpreter.
    code = """
import importlib
import pkgutil
import sys
sys.path.insert(0, sys.argv[1])
from proposal import engine
modules = tuple(pkgutil.walk_packages(engine.__path__, engine.__name__ + '.'))
assert modules
for module in modules:
    importlib.import_module(module.name)
from proposal.engine.converter.ingest import _resolve_unpack
from proposal.engine.hwpx.zip_surgery import unpack
assert _resolve_unpack() is unpack
assert not any(name == 'skills' or name.startswith('skills.') for name in sys.modules)
"""
    # When: loading the engine's modules, without executing any backend or pipeline.
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(mounted_proposal.parent)],
        cwd=mounted_proposal.parent, env={"HOME": str(mounted_proposal.parent)},
        capture_output=True, text=True, timeout=30, check=False,
    )
    # Then: sibling entry points cannot silently load a checkout-qualified engine.
    assert result.returncode == 0, result.stderr


def test_post_mount_scenario_runs_without_checkout_proposal(tmp_path: Path) -> None:
    from tests.unit.test_scenario_deployed_layout import _deployed_layout

    # Given: the deployed layout has shared runtime resources but no checkout proposal.
    live, scenario = _deployed_layout(tmp_path, "proposal")
    home = tmp_path / "home"
    home.mkdir()
    # When: execute the actual post-mount entry point with scrubbed credentials.
    result = subprocess.run(
        ("bash", str(scenario)), cwd=home,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin",
             "INTEROP_RUNTIME": str(ENGINE_ROOT.parents[2]),
             "AUTOPHAGY_DEMO_SECRET": "DUMMY-proposal-mounted",
             "AUTOPHAGY_SKILL_LIVE_ROOT": str(live)},
        capture_output=True, text=True, timeout=180, check=False,
    )
    # Then: fixture creation and every subsequent scenario stage must finish.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SCENARIO-PASS" in result.stdout


def test_the_engine_exposes_a_render_entry_point() -> None:
    render = importlib.import_module(f"{ENGINE_MODULE}.render")

    assert callable(getattr(render, "render_hwpx", None)), (
        "engine exposes no render_hwpx entry point"
    )
