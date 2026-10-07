from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, cast

import pytest

from automation import term_correction, term_glossary
from skills.proposal.engine.agents.llm import MockLLMClient, load_mock_responses
from skills.proposal.engine.hwpx.validate import text_extract
from skills.proposal.engine.pipeline import orchestrator
from skills.proposal.engine.render import engine_digest
from skills.proposal.scripts import proposal_render
from skills.proposal.scripts.proposal_config import ProposalConfig
from skills.proposal.scripts.proposal_ir import FigureSpec, figures_to_json
from skills.proposal.scripts.proposal_version import Staging, VersionStore


class _GeneratedTypoLLM:
    def __init__(self) -> None:
        responses = load_mock_responses(str(orchestrator.DEFAULT_LLM_FIXTURE))
        self._inner: MockLLMClient = MockLLMClient(responses)

    def complete(self, role: str, prompt: str) -> str:
        response = self._inner.complete(role, prompt)
        if role == "writer_sec2":
            return f"{response}\n근거 추출 자동화 전략을 설계한다."
        return response


class FakeRenderer:
    """Stands in for the in-tree engine and records what the skill handed it."""

    def __init__(self, *, error: Exception | None = None, writes_output: bool = True) -> None:
        self.error = error
        self.writes_output = writes_output
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.writes_output:
            out = Path(str(kwargs["out_path"]))
            out.parent.mkdir(parents=True, exist_ok=True)
            _ = out.write_bytes(b"rendered hwpx")
        return object()


def _config(tmp_path: Path) -> ProposalConfig:
    return ProposalConfig(
        profile="30-page",
        image_model="gpt-image-2",
        image_monthly_cap_usd=10,
        refine_pin="b" * 40,
        drive_root="outputs",
        state_root=tmp_path / "state",
    )


def _version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, bytes]:
    root = tmp_path / "proposals"
    store = VersionStore(root)
    staging = store.begin("demo", hashlib.sha256(str(root).encode()).hexdigest())
    assert isinstance(staging, Staging)
    version = store.promote(
        "demo",
        staging,
        {"parent": None, "request": {"profile": "30-page"}, "schema_version": 1},
    )
    path = root / "demo" / "versions" / version
    png = b"png bytes"
    figure = FigureSpec(
        "fig-s1-01",
        "s1",
        ("public:claim-1",),
        "public diagram",
        "caption",
        hashlib.sha256(png).hexdigest(),
        0,
    )
    records = cast(list[dict[str, object]], json.loads(figures_to_json((figure,))))
    records[0]["model"] = "gpt-image-2"
    _ = (path / "figures.json").write_text(json.dumps(records), encoding="utf-8")
    _ = (path / "images" / "fig-s1-01.png").write_bytes(png)
    drafts = {"sections": [{"section_id": "s1", "body": "public research body"}]}
    _ = (path / "out" / "drafts.json").write_text(json.dumps(drafts), encoding="utf-8")
    _ = (path / "out" / "drafts.refined.json").write_text(json.dumps(drafts), encoding="utf-8")
    _ = (path / "out" / "drafts.json.planspec.json").write_bytes(b"planspec sidecar")
    _ = (path / "out" / "drafts.json.pms.json").write_bytes(b"pms sidecar")
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    return path, png


def test_generated_document_corrects_new_prose_but_preserves_source_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    glossary = tmp_path / "용어집.csv"
    _ = glossary.write_text("근거 추출,근거 연결\n", encoding="utf-8")
    correction_log = tmp_path / "corrections.jsonl"
    monkeypatch.setenv("TERM_GLOSSARY_FILE", str(glossary))
    monkeypatch.setenv("TERM_CORRECTION_LOG", str(correction_log))
    monkeypatch.delenv("DRIVE_PUBLISH_ENABLED", raising=False)
    corpus = Path(orchestrator.PROJECT_ROOT) / "resource" / "gold"
    source = corpus / "prior_proposal.txt"
    original_source = source.read_bytes()
    artifact = tmp_path / "proposal.hwpx"

    result = orchestrator.run(
        corpus_dir=str(corpus),
        out_path=str(artifact),
        llm=_GeneratedTypoLLM(),
        project="demo",
    )

    quoted = (
        "KPI 목표는 name: 근거 추출 정확도; baseline: 90%; target: 95%; "
        "weight: 40%; method: 공개 근거 매칭 평가; env: 오프라인 골드셋으로 설정한다"
    )
    rendered = text_extract(str(artifact))
    rendered_drafts = cast(
        dict[str, object], json.loads(Path(result.drafts_path).read_text(encoding="utf-8"))
    )
    sections = cast(list[dict[str, object]], rendered_drafts["sections"])
    rendered_bodies = "\n".join(cast(str, section["body"]) for section in sections)
    citations = cast(
        dict[str, object], json.loads(Path(result.citations_path).read_text(encoding="utf-8"))
    )
    claims = cast(list[dict[str, str]], citations["claims"])
    records = [
        cast(dict[str, object], json.loads(line))
        for line in correction_log.read_text(encoding="utf-8").splitlines()
    ]

    assert "근거 연결 자동화 전략을 설계한다." in rendered
    assert "근거 추출 자동화 전략을 설계한다." not in rendered
    assert quoted in rendered
    assert quoted in rendered_bodies
    assert any(claim["claim_text"] == quoted for claim in claims)
    assert source.read_bytes() == original_source
    assert records == [
        {
            "after": "근거 연결",
            "at": records[0]["at"],
            "before": "근거 추출",
            "document": "proposal",
            "kind": "exact",
            "label": "근거 기반 KIMM 문서봇 플랜",
            "project": "demo",
            "stage": "generated-body",
            "term": "근거 연결",
        }
    ]


def test_proposal_term_boundary_does_not_opt_into_drive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DRIVE_PUBLISH_ENABLED", raising=False)
    monkeypatch.delenv("TERM_GLOSSARY_FILE", raising=False)
    monkeypatch.setenv("TERM_GLOSSARY_CACHE", str(tmp_path / "empty-cache"))
    monkeypatch.setattr(
        term_glossary,
        "_client",
        lambda: pytest.fail("proposal correction must not opt into Drive"),
    )
    corpus = Path(orchestrator.PROJECT_ROOT) / "resource" / "gold"
    artifact = tmp_path / "offline.hwpx"
    llm = MockLLMClient(load_mock_responses(str(orchestrator.DEFAULT_LLM_FIXTURE)))

    result = orchestrator.run(str(corpus), str(artifact), llm, project="demo")

    assert Path(result.artifact_path).is_file()


def test_proposal_term_boundary_fails_soft_without_partial_correction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    glossary = tmp_path / "용어집.csv"
    _ = glossary.write_text("근거 추출,근거 연결\n", encoding="utf-8")
    monkeypatch.setenv("TERM_GLOSSARY_FILE", str(glossary))
    monkeypatch.setenv("TERM_CORRECTION_LOG", str(tmp_path / "corrections.jsonl"))
    calls = 0
    original_apply = term_correction.apply

    def fail_after_one(
        text: str, pairs: term_correction.Glossary
    ) -> tuple[str, tuple[term_correction.Correction, ...]]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("correction unavailable")
        return original_apply(text, pairs)

    monkeypatch.setattr(term_correction, "apply", fail_after_one)
    corpus = Path(orchestrator.PROJECT_ROOT) / "resource" / "gold"
    artifact = tmp_path / "fail-soft.hwpx"

    _ = orchestrator.run(str(corpus), str(artifact), _GeneratedTypoLLM(), project="demo")

    rendered = text_extract(str(artifact))
    assert "근거 추출 자동화 전략을 설계한다." in rendered
    assert "근거 연결 자동화 전략을 설계한다." not in rendered
    assert not (tmp_path / "corrections.jsonl").exists()
    assert "TERM-CORRECTION-SKIP document=proposal reason=RuntimeError" in capsys.readouterr().err


def test_config_carries_no_external_engine_checkout(tmp_path: Path) -> None:
    cfg = _config(tmp_path)

    for retired in ("docbot_root", "docbot_pin", "seed_hwpx_relpath", "seed_sha256"):
        assert not hasattr(cfg, retired), f"{retired} outlived the external checkout"


def test_missing_figure_exits_five_and_lists_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    (version / "images" / "fig-s1-01.png").unlink()
    renderer = FakeRenderer()

    with pytest.raises(SystemExit) as raised:
        _ = proposal_render.run_render("demo", renderer=renderer, config=_config(tmp_path))

    assert raised.value.code == 5
    assert "fig-s1-01" in capsys.readouterr().err
    assert renderer.calls == []


def test_happy_path_hands_the_engine_every_input_and_updates_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    renderer = FakeRenderer()

    result = proposal_render.run_render(
        "demo", mode="live", renderer=renderer, config=_config(tmp_path)
    )

    call = renderer.calls[0]
    assert Path(str(call["drafts_path"])) == version / "out" / "drafts.refined.json"
    assert Path(str(call["corpus_dir"])) == version / "corpus"
    assert Path(str(call["out_path"])) == version / "out" / "proposal.hwpx"
    assert Path(str(call["images_dir"])) == version / "images"
    assert Path(str(call["figures_path"])) == version / "figures.json"
    assert Path(str(call["tables_path"])) == version / "tables.json"
    assert call["profile"] == "30-page"
    assert call["project"] == "demo"

    digest = hashlib.sha256(b"rendered hwpx").hexdigest()
    assert result.hwpx_sha256 == digest
    assert result.engine_sha == engine_digest()
    manifest = json.loads((version / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["hwpx_sha256"] == digest
    assert manifest["engine_sha"] == engine_digest()
    assert manifest["profile"] == "30-page"
    assert manifest["refined"] is True
    assert manifest["draft_preview"] is False


def test_manifest_records_a_reproducible_in_tree_engine_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)

    _ = proposal_render.run_render("demo", renderer=FakeRenderer(), config=_config(tmp_path))

    manifest = json.loads((version / "manifest.json").read_text(encoding="utf-8"))
    recorded = str(manifest["engine_sha"])
    assert len(recorded) == 64 and set(recorded) <= set("0123456789abcdef")
    assert recorded == engine_digest(), "the digest must be reproducible from this checkout alone"


def test_engine_written_outputs_are_private_under_permissive_umask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)

    def writing_renderer(**kwargs: Any) -> object:
        output = Path(str(kwargs["out_path"]))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.unlink(missing_ok=True)
        _ = output.write_bytes(b"rendered hwpx")
        sidecar = output.parent / "engine-sidecar.json"
        _ = sidecar.write_text("{}\n", encoding="utf-8")
        output.chmod(0o664)
        sidecar.chmod(0o664)
        return object()

    previous_umask = os.umask(0o022)
    try:
        _ = proposal_render.run_render(
            "demo", renderer=writing_renderer, config=_config(tmp_path)
        )
    finally:
        _ = os.umask(previous_umask)

    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in (version / "out").rglob("*")
        if path.is_file()
    )


def test_refined_drafts_provision_sidecars_before_the_engine_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    expected = {".planspec.json": b"planspec sidecar", ".pms.json": b"pms sidecar"}

    def checking_renderer(**kwargs: Any) -> object:
        drafts_path = Path(str(kwargs["drafts_path"]))
        for suffix, content in expected.items():
            assert Path(f"{drafts_path}{suffix}").read_bytes() == content
        _ = Path(str(kwargs["out_path"])).write_bytes(b"rendered hwpx")
        return object()

    _ = proposal_render.run_render("demo", renderer=checking_renderer, config=_config(tmp_path))

    for suffix, content in expected.items():
        assert (version / "out" / f"drafts.refined.json{suffix}").read_bytes() == content


def test_refined_drafts_fail_closed_when_source_sidecar_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    (version / "out" / "drafts.json.pms.json").unlink()
    renderer = FakeRenderer()

    with pytest.raises(
        proposal_render.RenderInputError,
        match="refined drafts sidecar source is missing: drafts.json.pms.json",
    ):
        _ = proposal_render.run_render("demo", renderer=renderer, config=_config(tmp_path))

    assert renderer.calls == []
    assert not (version / "out" / "drafts.refined.json.planspec.json").exists()


def test_raw_drafts_require_legitimate_refine_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    (version / "out" / "drafts.refined.json").unlink()
    _ = (version / "out" / "refine-report.json").write_text(
        json.dumps({"refined": False, "reason": "no-non-glm-host"}), encoding="utf-8"
    )
    renderer = FakeRenderer()

    result = proposal_render.run_render("demo", renderer=renderer, config=_config(tmp_path))

    assert Path(str(renderer.calls[0]["drafts_path"])) == version / "out" / "drafts.json"
    assert result.refined is False
    manifest = json.loads((version / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["refined"] is False


def test_missing_refined_drafts_without_skip_record_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    (version / "out" / "drafts.refined.json").unlink()
    renderer = FakeRenderer()

    with pytest.raises(proposal_render.RenderInputError, match="refined drafts"):
        _ = proposal_render.run_render("demo", renderer=renderer, config=_config(tmp_path))

    assert renderer.calls == []


def test_allow_missing_figures_marks_draft_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    (version / "images" / "fig-s1-01.png").unlink()

    result = proposal_render.run_render(
        "demo", allow_missing_figures=True, renderer=FakeRenderer(), config=_config(tmp_path)
    )

    assert result.draft_preview is True
    manifest = json.loads((version / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["draft_preview"] is True


def test_a_failed_render_writes_no_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    original_manifest = (version / "manifest.json").read_bytes()

    with pytest.raises(proposal_render.RenderProcessError, match="engine exploded"):
        _ = proposal_render.run_render(
            "demo",
            renderer=FakeRenderer(error=RuntimeError("engine exploded")),
            config=_config(tmp_path),
        )

    assert (version / "manifest.json").read_bytes() == original_manifest


def test_engine_failure_is_explicit_and_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _version(tmp_path, monkeypatch)
    renderer = FakeRenderer(error=RuntimeError("engine exploded"))

    with pytest.raises(proposal_render.RenderProcessError, match="engine exploded"):
        _ = proposal_render.run_render("demo", renderer=renderer, config=_config(tmp_path))

    assert len(renderer.calls) == 1


def test_engine_success_without_an_artifact_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    original_manifest = (version / "manifest.json").read_bytes()

    with pytest.raises(proposal_render.RenderProcessError, match="without an HWPX output"):
        _ = proposal_render.run_render(
            "demo", renderer=FakeRenderer(writes_output=False), config=_config(tmp_path)
        )

    assert (version / "manifest.json").read_bytes() == original_manifest


def test_malformed_figures_is_clean_input_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    _ = (version / "figures.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(proposal_render.RenderInputError, match="figures.json"):
        _ = proposal_render.run_render(
            "demo", renderer=FakeRenderer(), config=_config(tmp_path)
        )


def test_cover_overrides_reach_the_engine_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    _ = (version / "cover.json").write_text(
        json.dumps({"classification": "기계·로봇"}, ensure_ascii=False), encoding="utf-8"
    )
    renderer = FakeRenderer()

    _ = proposal_render.run_render("demo", renderer=renderer, config=_config(tmp_path))

    assert renderer.calls[0]["cover_overrides"] == {"classification": "기계·로봇"}


def test_cover_overrides_are_absent_when_no_cover_manifest_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _version(tmp_path, monkeypatch)
    renderer = FakeRenderer()

    _ = proposal_render.run_render("demo", renderer=renderer, config=_config(tmp_path))

    assert renderer.calls[0]["cover_overrides"] is None
