from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from ..contracts.protocols import LLMClient
from . import orchestrator

if TYPE_CHECKING:
    from pathlib import Path

    from ..contracts.collab_models import AdviseRanking, EvidenceCandidate

# Default cache lives alongside the deterministic mock fixture so replay works
# offline with no key. Resolved to an absolute path so cwd does not matter.
DEFAULT_CACHE_PATH = str(orchestrator.DEFAULT_LLM_FIXTURE)
_VALID_MODES = ("live", "replay", "record")
# Env vars probed for a record-mode key (mirrors agents.llm.RealLLMClient).
_API_KEY_ENV = ("ANTHROPIC_API_KEY", "KIMM_DOCBOT_LLM_API_KEY")
# critic/judge are advisory: an empty result == deterministic MockLLM behaviour,
# so their replay cache-miss is softened; essential roles still raise on a miss.
_ADVISORY_ROLES = frozenset({"advise_scorer", "critic", "judge"})


class _ReplayGracefulLLM:
    # Not a CachingLLMClient instance, so orchestrator.run skips its LLM critic
    # (isinstance gate) and a replay `run` reproduces MockLLM output byte-for-byte.
    def __init__(self, inner: LLMClient) -> None:
        self._inner: LLMClient = inner
        self.fallback_roles: set[str] = set()

    def complete(self, role: str, prompt: str) -> str:
        from ..agents.llm import CacheMissError

        try:
            return self._inner.complete(role, prompt)
        except CacheMissError:
            if role in _ADVISORY_ROLES:
                self.fallback_roles.add(role)
                return ""
            raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    command = cast(str, args.command)
    if command == "run":
        return _run(
            corpus_dir=cast(str, args.corpus),
            out_path=cast(str, args.out),
            mode=cast(str, args.mode),
            cache_path=cast(str, args.cache),
            provider=cast(str, args.provider),
        )
    if command == "draft":
        return _draft(
            corpus_dir=cast(str, args.corpus),
            out_path=cast(str, args.out),
            mode=cast(str, args.mode),
            cache_path=cast(str, args.cache),
            provider=cast(str, args.provider),
        )
    if command == "render":
        return _render_command(
            drafts_path=cast(str, args.drafts),
            corpus_dir=cast(str, args.corpus),
            out_path=cast(str, args.out),
            profile=cast(str | None, args.profile),
            images_dir=cast(str | None, args.images),
            figures_path=cast(str | None, args.figures),
            tables_path=cast(str | None, args.tables),
            cover_path=cast(str | None, args.cover),
            mode=cast(str, args.mode),
        )
    if command == "converge":
        return _converge_command(
            drafts_path=cast(str, args.drafts),
            out_path=cast(str, args.out),
            profile=cast(str, args.profile),
            images_dir=cast(str | None, args.images),
            figures_path=cast(str | None, args.figures),
            measurer=cast(str, args.measurer),
            pages=cast(int, args.pages),
        )
    if command == "judge":
        return _judge(
            hwpx_path=cast(str, args.hwpx),
            mode=cast(str, args.mode),
            cache_path=cast(str, args.cache),
            out_path=cast(str, args.out),
        )
    if command == "preview":
        from ..hwpx.visual_preview import main as preview_main

        preview_argv = [cast(str, args.hwpx), "--out-dir", cast(str, args.out_dir)]
        if args.chrome is not None:
            preview_argv.extend(["--chrome", cast(str, args.chrome)])
        return preview_main(preview_argv)
    if command == "research-convert":
        return _research_convert(
            synthesis=cast(str, args.synthesis), out=cast(str, args.out),
        )
    if command == "research":
        return _research(
            synthesis=cast(str, args.synthesis),
            out=cast(str, args.out),
            accept=cast(list[str] | None, args.accept),
            ranking_out=cast(str | None, args.ranking_out),
            mode=cast(str, args.mode),
            cache_path=cast(str, args.cache),
            provider=cast(str, args.provider),
        )
    if command == "workflow":
        return _workflow(
            corpus_dir=cast(str, args.corpus),
            synthesis=cast(str | None, args.synthesis),
            out_path=cast(str, args.out),
            accept_policy=cast(str, args.accept_policy),
            mode=cast(str, args.mode),
            cache_path=cast(str, args.cache),
            provider=cast(str, args.provider),
        )
    if command == "corpus-lint":
        return _corpus_lint(
            corpus=cast(str, args.corpus),
            candidate_dir=cast(str, args.candidate_dir),
            warn_only=cast(bool, args.warn_only),
        )
    if command == "merge":
        from .merge_cli import merge

        return merge(
            bundle=cast(str, args.bundle),
            opinions_dir=cast(str, args.opinions),
            decisions_path=cast(str, args.decisions),
            out_path=cast(str, args.out),
        )

    parser.print_help(sys.stderr)
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kimm-docbot")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run the gold proposal pipeline")
    _ = run_parser.add_argument("--corpus", required=True, help="input corpus directory")
    _ = run_parser.add_argument("--out", required=True, help="output HWPX path")
    _add_llm_args(run_parser)

    draft_parser = subparsers.add_parser("draft", help="draft sections without rendering HWPX")
    _ = draft_parser.add_argument("--corpus", required=True, help="input corpus directory")
    _ = draft_parser.add_argument("--out", required=True, help="output drafts JSON path")
    _add_llm_args(draft_parser)

    render_parser = subparsers.add_parser("render", help="render an editable drafts JSON bundle")
    _ = render_parser.add_argument("--drafts", required=True, help="input drafts JSON path")
    _ = render_parser.add_argument("--corpus", required=True, help="input corpus directory")
    _ = render_parser.add_argument("--out", required=True, help="output HWPX path")
    _ = render_parser.add_argument(
        "--profile",
        choices=sorted(orchestrator.VALID_PROFILES),
        default=None,
        help="document profile (valid: 10-page, 30-page)",
    )
    _ = render_parser.add_argument("--images", default=None, help="image asset directory")
    _ = render_parser.add_argument("--figures", default=None, help="figure manifest JSON path")
    _ = render_parser.add_argument("--tables", default=None, help="table manifest JSON path")
    _ = render_parser.add_argument(
        "--cover",
        default=None,
        help="flat JSON map overriding derived cover values",
    )
    _ = render_parser.add_argument(
        "--mode",
        choices=_VALID_MODES,
        default="live",
        help="accepted for command symmetry; rendering performs no LLM calls",
    )

    converge_parser = subparsers.add_parser(
        "converge", help="bounded render/measure refinement for a page budget"
    )
    _ = converge_parser.add_argument("--drafts", required=True, help="input drafts JSON path")
    _ = converge_parser.add_argument("--out", required=True, help="best-candidate HWPX path")
    _ = converge_parser.add_argument(
        "--profile", choices=sorted(orchestrator.VALID_PROFILES), default="30-page"
    )
    _ = converge_parser.add_argument("--images", default=None, help="image asset directory")
    _ = converge_parser.add_argument("--figures", default=None, help="figure manifest JSON path")
    _ = converge_parser.add_argument(
        "--measurer", choices=("auto", "estimate", "soffice"), default="auto"
    )
    _ = converge_parser.add_argument("--pages", type=int, default=30, help="target page count")

    judge_parser = subparsers.add_parser(
        "judge",
        help="LLM-judge an HWPX proposal against the rule.md rubric (opt-in, non-CI)",
    )
    _ = judge_parser.add_argument("hwpx", help="path to the HWPX proposal to judge")
    _ = judge_parser.add_argument(
        "--out", default="judge.json", help="output JSON path (default: judge.json)"
    )
    _add_llm_args(judge_parser, with_provider=False)

    preview_parser = subparsers.add_parser(
        "preview",
        help="render an HWPX into paginated HTML, PDF, and PNGs for visual review",
    )
    _ = preview_parser.add_argument("hwpx", help="input HWPX path")
    _ = preview_parser.add_argument(
        "--out-dir", required=True, help="directory for preview.html, preview.pdf, and pages/"
    )
    _ = preview_parser.add_argument("--chrome", metavar="PATH", help="Chrome/Chromium binary path")

    rc_parser = subparsers.add_parser(
        "research-convert",
        help="convert ultraresearch SYNTHESIS.md to corpus files",
    )
    _ = rc_parser.add_argument("synthesis", help="path to SYNTHESIS.md")
    _ = rc_parser.add_argument("--out", required=True, help="output corpus directory")

    research_parser = subparsers.add_parser(
        "research",
        help="Convert SYNTHESIS.md, score candidates, and optionally accept (promote) to corpus",
    )
    _ = research_parser.add_argument("synthesis", help="path to SYNTHESIS.md")
    _ = research_parser.add_argument("--out", required=True, help="output corpus directory")
    _ = research_parser.add_argument(
        "--accept",
        nargs="*",
        metavar="CANDIDATE_ID",
        help="candidate_ids to accept and promote to corpus",
    )
    _ = research_parser.add_argument(
        "--ranking-out",
        default=None,
        help="path to save advise ranking JSON (optional; defaults to stdout)",
    )
    _add_llm_args(research_parser)

    workflow_parser = subparsers.add_parser(
        "workflow",
        help="4-stage unattended: research→draft→parallel-revise→render",
    )
    _ = workflow_parser.add_argument("--corpus", required=True, help="input corpus directory")
    _ = workflow_parser.add_argument(
        "--synthesis",
        default=None,
        help="optional SYNTHESIS.md path (ultraresearch output); skips research if omitted",
    )
    _ = workflow_parser.add_argument("--out", required=True, help="output HWPX path")
    _ = workflow_parser.add_argument(
        "--accept-policy",
        default="none",
        dest="accept_policy",
        help="auto-accept policy: topk:N, threshold:X, or none (default: none)",
    )
    _add_llm_args(workflow_parser)

    cl_parser = subparsers.add_parser(
        "corpus-lint",
        help="pre-ingest lint: detect numeric conflicts in candidate files",
    )
    _ = cl_parser.add_argument("--corpus", required=True, help="existing corpus directory")
    _ = cl_parser.add_argument(
        "--candidate-dir", required=True, help="directory with candidate corpus files"
    )
    _ = cl_parser.add_argument(
        "--warn-only",
        action="store_true",
        help="print warnings but always exit 0 (non-blocking mode)",
    )

    merge_parser = subparsers.add_parser(
        "merge",
        help="Apply reviewer decisions to a DraftBundle and re-render HWPX",
    )
    _ = merge_parser.add_argument(
        "--bundle",
        required=True,
        help=(
            "DraftBundle prefix (e.g. out/proposal.hwpx — loads .planspec.json, "
            ".drafts.json, .pms.json)"
        ),
    )
    _ = merge_parser.add_argument(
        "--opinions",
        required=True,
        help="Directory containing reviewer opinion .md files",
    )
    _ = merge_parser.add_argument(
        "--decisions",
        required=True,
        help="JSON file with hunk decisions",
    )
    _ = merge_parser.add_argument(
        "--out",
        required=True,
        help="Output HWPX path",
    )

    return parser


def _add_llm_args(parser: argparse.ArgumentParser, *, with_provider: bool = True) -> None:
    _ = parser.add_argument(
        "--mode",
        choices=_VALID_MODES,
        default="live",
        help="LLM mode: live (real calls, default), replay (offline cache), record (real + cache update)",
    )
    _ = parser.add_argument(
        "--cache",
        default=DEFAULT_CACHE_PATH,
        help=f"path to LLM cache JSON for replay/record mode (default: {DEFAULT_CACHE_PATH})",
    )
    if with_provider:
        _ = parser.add_argument(
            "--provider", default="hermes", help="LLM provider (default: hermes)"
        )



def _build_llm(*, mode: str, cache_path: str, provider: str = "hermes") -> LLMClient:
    """Construct the Hermes LLM client.

    Hermes authenticates through the node's agent account, so no API key is read
    here — this repository routes every model call through that one path.
    """
    backend = os.environ.get("KIMM_DOCBOT_LLM_BACKEND", "hermes").strip().lower()
    if provider == "hermes":
        backend = "hermes"
    if backend != "hermes":
        raise ValueError(f"Unsupported LLM backend: {backend!r}")
    from ..agents.llm import get_llm

    if mode == "live":
        return get_llm({"mode": "live", "provider": backend})

    llm = get_llm({"mode": mode, "cache_path": cache_path, "provider": backend})
    if mode == "replay":
        return _ReplayGracefulLLM(llm)
    return llm


def _run(
    *, corpus_dir: str, out_path: str, mode: str, cache_path: str, provider: str
) -> int:
    _write_stdout(event="pipeline_start", corpus=corpus_dir, out=out_path, mode=mode)
    try:
        llm = _build_llm(mode=mode, cache_path=cache_path, provider=provider)
        result = orchestrator.run(corpus_dir=corpus_dir, out_path=out_path, llm=llm)
    except (ValueError, RuntimeError) as exc:
        _write_stdout(event="pipeline_failed", error=str(exc))
        return 1

    for node in result.node_log:
        _write_stdout(event="node_completed", node=node.node_name)

    _write_stdout(
        event="pipeline_completed",
        artifact_path=result.artifact_path,
        citations_path=result.citations_path,
    )
    return 0


def _draft(
    *, corpus_dir: str, out_path: str, mode: str, cache_path: str, provider: str
) -> int:
    _write_stdout(event="draft_start", corpus=corpus_dir, out=out_path, mode=mode)
    try:
        llm = _build_llm(mode=mode, cache_path=cache_path, provider=provider)
        result = orchestrator.draft(corpus_dir=corpus_dir, out_path=out_path, llm=llm)
    except (OSError, ValueError, RuntimeError) as exc:
        _write_stdout(event="pipeline_failed", error=str(exc))
        return 1

    for node in result.node_log:
        _write_stdout(event="node_completed", node=node.node_name)
    _write_stdout(
        event="draft_completed",
        drafts_path=result.drafts_path,
        planspec_path=result.planspec_path,
        pms_path=result.pms_path,
    )
    return 0


def _render_command(
    *,
    drafts_path: str,
    corpus_dir: str,
    out_path: str,
    profile: str | None,
    images_dir: str | None,
    figures_path: str | None,
    tables_path: str | None,
    cover_path: str | None,
    mode: str,
) -> int:
    from pathlib import Path

    _write_stdout(
        event="render_start",
        drafts=drafts_path,
        corpus=corpus_dir,
        out=out_path,
        profile=profile,
        images=images_dir,
        figures=figures_path,
        tables=tables_path,
        cover=cover_path,
        mode=mode,
    )
    try:
        result = orchestrator.render(
            drafts_path=drafts_path,
            corpus_dir=corpus_dir,
            out_path=out_path,
            profile=profile,
            images_dir=Path(images_dir) if images_dir is not None else None,
            figures_path=Path(figures_path) if figures_path is not None else None,
            tables_path=Path(tables_path) if tables_path is not None else None,
            cover_overrides=_load_cover_values(cover_path),
        )
    except (OSError, ValueError, RuntimeError) as exc:
        _write_stdout(event="pipeline_failed", error=str(exc))
        return 1

    for node in result.node_log:
        _write_stdout(event="node_completed", node=node.node_name)
    _write_stdout(
        event="pipeline_completed",
        artifact_path=result.artifact_path,
        citations_path=result.citations_path,
    )
    return 0


def _converge_command(
    *,
    drafts_path: str,
    out_path: str,
    profile: str,
    images_dir: str | None,
    figures_path: str | None,
    measurer: str,
    pages: int,
) -> int:
    from .page_convergence import converge_from_paths

    try:
        result = converge_from_paths(
            drafts_path=drafts_path,
            out_path=out_path,
            profile=profile,
            images_dir=images_dir,
            figures_path=figures_path,
            measurer_name=measurer,
            target=pages,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        _write_stdout(event="page_convergence_failed", error=str(exc), publishable=False)
        return 1
    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if result.status == "converged" else 1


def _judge(*, hwpx_path: str, mode: str, cache_path: str, out_path: str) -> int:
    from ..agents.judge import judge_proposal
    from ..hwpx.validate import text_extract

    _write_stdout(event="judge_start", hwpx=hwpx_path, out=out_path, mode=mode)
    try:
        llm = _build_llm(mode=mode, cache_path=cache_path)
        text = text_extract(hwpx_path)
        result = judge_proposal(text, llm, threshold=3)
    except (ValueError, RuntimeError) as exc:
        # CacheMissError (replay, no recorded judge entry) and key/backend errors
        # land here; message is already key-free.
        _write_stdout(event="judge_failed", error=str(exc))
        return 1
    except FileNotFoundError as exc:
        _write_stdout(event="judge_failed", error=str(exc))
        return 1

    if isinstance(llm, _ReplayGracefulLLM) and "judge" in llm.fallback_roles:
        _write_stdout(
            event="judge_cache_unpopulated",
            note="no judge entry in cache; emitted neutral placeholder scores (use --mode record)",
        )

    _write_json(out_path, result.model_dump())
    _write_stdout(
        event="judge_completed",
        out=out_path,
        total_score=result.total_score,
        capped_score=result.capped_score,
        below_threshold=list(result.below_threshold),
    )
    return 0


def _research_convert(*, synthesis: str, out: str) -> int:
    from ..converter.research_convert import main as rc_main

    return rc_main([synthesis, "--out", out])


def _research(
    *,
    synthesis: str,
    out: str,
    accept: list[str] | None,
    ranking_out: str | None,
    mode: str,
    cache_path: str,
    provider: str,
) -> int:
    from pathlib import Path

    from ..agents.advise import score_candidates
    from ..agents.llm import CacheMissError, MockLLMClient
    from ..contracts.ids import stable_id
    from ..converter.ingest import ingest_dir
    from ..converter.materialize import materialize
    from ..converter.normalize import normalize
    from ..converter.pms import ProposalMaterialStore
    from ..converter.research_convert import convert_with_candidates

    _write_stdout(event="research_start", synthesis=synthesis, out=out)

    synthesis_path = Path(synthesis)
    if not synthesis_path.exists():
        _write_stdout(event="research_failed", error=f"{synthesis} not found")
        return 1

    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate_dir = out_dir / ".research_candidates"

    try:
        rc, candidates = convert_with_candidates(synthesis_path, candidate_dir)
    except OSError as exc:
        _write_stdout(event="research_failed", error=str(exc))
        return 1

    if rc != 0:
        _write_stdout(event="research_failed", error="SYNTHESIS conversion failed")
        return rc

    _write_stdout(event="research_candidates", count=len(candidates))

    try:
        llm = _build_llm(mode=mode, cache_path=cache_path, provider=provider)
    except ValueError as exc:
        llm = MockLLMClient({})
        _write_stdout(event="research_advise_fallback", reason=str(exc))

    try:
        ranking = score_candidates(candidates, llm)
    except (CacheMissError, KeyError) as exc:
        with MockLLMClient.record_mode():
            ranking = score_candidates(candidates, MockLLMClient({}))
        _write_stdout(event="research_advise_fallback", reason=str(exc))

    ranking_payload = {
        "candidates": [
            {
                "bucket": candidate.bucket,
                "candidate_id": candidate.candidate_id,
                "source_url": candidate.source_url,
            }
            for candidate in candidates
        ],
        "ranked": list(ranking.ranked),
        "rationale": ranking.rationale,
    }

    if ranking_out:
        _write_json(ranking_out, ranking_payload)
    else:
        print(json.dumps(ranking_payload, ensure_ascii=False, sort_keys=True, indent=2))

    _write_stdout(event="research_ranking_done", total=len(ranking.ranked))

    if accept:
        cand_by_id = {candidate.candidate_id: candidate for candidate in candidates}
        accepted_count = 0

        for candidate_id in sorted(set(accept)):
            candidate = cand_by_id.get(candidate_id)
            if candidate is None:
                _write_stdout(
                    event="research_accept_skip",
                    candidate_id=candidate_id,
                    reason="not found",
                )
                continue

            source_file = candidate_dir / f"research-{stable_id(candidate.source_url)[:8]}.md"
            target_file = out_dir / source_file.name
            if not source_file.exists():
                _write_stdout(
                    event="research_accept_skip",
                    candidate_id=candidate_id,
                    reason="candidate corpus file not found",
                )
                continue

            _ = target_file.write_text(source_file.read_text(encoding="utf-8"), encoding="utf-8")
            raw_docs = ingest_dir(str(out_dir))
            docs = [normalize(raw) for raw in raw_docs]
            units = materialize(docs)
            pms = ProposalMaterialStore(units)

            promoted_any = False
            for unit in units:
                for provenance in unit.provenances:
                    if provenance.source_url == candidate.source_url:
                        promoted_any = pms.promote_to_public(unit.unit_id) or promoted_any

            if promoted_any:
                accepted_count += 1
                _write_stdout(
                    event="research_accepted",
                    candidate_id=candidate_id,
                    source_url=candidate.source_url,
                )

        _write_stdout(event="research_accept_done", accepted=accepted_count)

    _write_stdout(event="research_completed", out=str(out_dir))
    return 0


def _workflow(
    *,
    corpus_dir: str,
    synthesis: str | None,
    out_path: str,
    accept_policy: str,
    mode: str,
    cache_path: str,
    provider: str,
) -> int:
    import shutil
    import tempfile
    from pathlib import Path

    _write_stdout(event="workflow_start", corpus=corpus_dir, synthesis=synthesis, out=out_path)

    accepted_candidates: list[str] = []
    if synthesis is None:
        return _workflow_run(
            overlay_corpus=corpus_dir,
            out_path=out_path,
            accepted_candidates=accepted_candidates,
            mode=mode,
            cache_path=cache_path,
            provider=provider,
        )

    synthesis_path = Path(synthesis)
    if not synthesis_path.exists():
        _write_stdout(event="workflow_failed", step="research", error=f"{synthesis} not found")
        return 1

    try:
        with tempfile.TemporaryDirectory(prefix="kimm_wf_overlay_") as tmpdir:
            overlay_dir = Path(tmpdir)
            for item in sorted(Path(corpus_dir).iterdir()):
                if item.is_file():
                    _ = shutil.copy2(item, overlay_dir / item.name)

            rc, accepted_candidates = _workflow_research_phase(
                synthesis_path=synthesis_path,
                overlay_dir=overlay_dir,
                accept_policy=accept_policy,
                mode=mode,
                cache_path=cache_path,
                provider=provider,
            )
            if rc != 0:
                return rc

            return _workflow_run(
                overlay_corpus=str(overlay_dir),
                out_path=out_path,
                accepted_candidates=accepted_candidates,
                mode=mode,
                cache_path=cache_path,
                provider=provider,
            )
    except OSError as exc:
        _write_stdout(event="workflow_failed", step="research_phase", error=str(exc))
        return 1


def _workflow_research_phase(
    *,
    synthesis_path: "Path",
    overlay_dir: "Path",
    accept_policy: str,
    mode: str,
    cache_path: str,
    provider: str,
) -> tuple[int, list[str]]:
    import shutil

    from ..agents.advise import score_candidates
    from ..agents.llm import CacheMissError, MockLLMClient
    from ..contracts.ids import stable_id
    from ..converter.ingest import ingest_dir
    from ..converter.materialize import materialize
    from ..converter.normalize import normalize
    from ..converter.pms import ProposalMaterialStore
    from ..converter.research_convert import convert_with_candidates

    candidate_dir = overlay_dir / ".research_candidates"
    rc, candidates = convert_with_candidates(synthesis_path, candidate_dir)
    if rc != 0:
        _write_stdout(event="workflow_failed", step="synthesis_convert", error="conversion failed")
        return rc, []

    _write_stdout(event="workflow_candidates", count=len(candidates))

    try:
        llm = _build_llm(mode=mode, cache_path=cache_path, provider=provider)
    except ValueError as exc:
        llm = MockLLMClient({})
        _write_stdout(event="workflow_advise_fallback", reason=str(exc))

    try:
        ranking = score_candidates(candidates, llm)
    except (CacheMissError, KeyError) as exc:
        with MockLLMClient.record_mode():
            ranking = score_candidates(candidates, MockLLMClient({}))
        _write_stdout(event="workflow_advise_fallback", reason=str(exc))

    accepted_candidates = _apply_accept_policy(accept_policy, candidates, ranking)
    _write_stdout(event="workflow_accepted", count=len(accepted_candidates))

    if not accepted_candidates:
        return 0, accepted_candidates

    for candidate_id in accepted_candidates:
        candidate = next((item for item in candidates if item.candidate_id == candidate_id), None)
        if candidate is None:
            continue
        source_file = candidate_dir / f"research-{stable_id(candidate.source_url)[:8]}.md"
        if source_file.exists():
            _ = shutil.copy2(source_file, overlay_dir / source_file.name)

    raw_docs = ingest_dir(str(overlay_dir))
    docs = [normalize(raw) for raw in raw_docs]
    units = materialize(docs)
    pms = ProposalMaterialStore(units)
    cand_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    for candidate_id in accepted_candidates:
        candidate = cand_by_id.get(candidate_id)
        if candidate is None:
            continue
        for unit in units:
            for provenance in unit.provenances:
                if provenance.source_url == candidate.source_url:
                    try:
                        _ = pms.promote_to_public(unit.unit_id)
                    except ValueError:
                        _write_stdout(
                            event="workflow_accept_skip",
                            candidate_id=candidate_id,
                            reason="redact unit cannot be promoted",
                        )

    return 0, accepted_candidates


def _workflow_run(
    *,
    overlay_corpus: str,
    out_path: str,
    accepted_candidates: list[str],
    mode: str,
    cache_path: str,
    provider: str,
) -> int:
    try:
        llm = _build_llm(mode=mode, cache_path=cache_path, provider=provider)
    except ValueError as exc:
        _write_stdout(event="workflow_failed", step="build_llm", error=str(exc))
        return 1

    try:
        result = orchestrator.run(corpus_dir=overlay_corpus, out_path=out_path, llm=llm)
    except (ValueError, RuntimeError) as exc:
        _write_stdout(event="workflow_failed", step="run", error=str(exc))
        return 1

    for node in result.node_log:
        _write_stdout(event="node_completed", node=node.node_name)

    _write_stdout(
        event="workflow_completed",
        artifact_path=result.artifact_path,
        citations_path=result.citations_path,
        accepted_candidates=len(accepted_candidates),
    )
    return 0


def _apply_accept_policy(
    policy: str,
    candidates: list["EvidenceCandidate"],
    ranking: "AdviseRanking",
) -> list[str]:
    if policy == "none" or not candidates:
        return []

    ranked = list(ranking.ranked)

    if policy.startswith("topk:"):
        try:
            k = int(policy[5:])
        except ValueError:
            return []
        return ranked[:k]

    if policy.startswith("threshold:"):
        try:
            _ = float(policy[10:])
        except ValueError:
            return []
        return ranked

    return []


def _corpus_lint(*, corpus: str, candidate_dir: str, warn_only: bool) -> int:
    from ..converter.corpus_lint import main as cl_main

    argv = ["--corpus", corpus, "--candidate-dir", candidate_dir]
    if warn_only:
        argv.append("--warn-only")
    return cl_main(argv)


def _load_cover_values(path: str | None) -> dict[str, str] | None:
    if path is None:
        return None
    from pathlib import Path

    payload = cast(object, json.loads(Path(path).read_text(encoding="utf-8")))
    if not isinstance(payload, dict):
        raise ValueError("cover JSON must contain a flat object")
    values: dict[str, str] = {}
    for key, value in cast(dict[object, object], payload).items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("cover JSON keys and values must be strings")
        values[key] = value
    return values


def _write_json(path: str, payload: object) -> None:
    from pathlib import Path

    out = Path(path)
    if out.parent != Path():
        out.parent.mkdir(parents=True, exist_ok=True)
    _ = out.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_stdout(**payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
