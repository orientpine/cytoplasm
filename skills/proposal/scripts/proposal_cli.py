#!/usr/bin/env python3
"""Private command surface for the personal proposal drafting workspace."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from automation.knowledge.pack import EvidencePack


_SCRIPT_DIR = Path(__file__).absolute().parent
if __package__ in (None, ""):
    sys.path.insert(0, str(_SCRIPT_DIR.parents[2]))
    sys.path.insert(0, str(_SCRIPT_DIR.parents[1]))
    __package__ = "proposal.scripts"

from . import proposal_assembly, proposal_core, proposal_dm, proposal_kanban, proposal_knowledge, proposal_llm, proposal_preflight, proposal_prompts, proposal_sensitivity  # noqa: E402
from . import proposal_env, proposal_images, proposal_governed  # noqa: E402
from .proposal_corpus import command as corpus_command  # noqa: E402
from .proposal_research import command as research_command  # noqa: E402
from .proposal_storage import ProposalError, ProposalPaths, Section, load_proposal  # noqa: E402


def _paths() -> ProposalPaths:
    return ProposalPaths.from_environment()


def _section_specs(values: list[str]) -> tuple[tuple[str, str], ...]:
    specs: list[tuple[str, str]] = []
    for value in values:
        key, separator, title = value.partition(":")
        if not separator or not key.strip() or not title.strip():
            raise ProposalError("sections must use key:title")
        specs.append((key.strip(), title.strip()))
    return tuple(specs)


def _kanban(slug: str) -> proposal_kanban.KanbanClient | None:
    if os.environ.get("PROPOSAL_KANBAN_DISABLED") == "1":
        return None
    return proposal_kanban.KanbanClient(proposal_kanban.board_name(slug))


def _complete_card(client: proposal_kanban.KanbanClient | None, section: Section) -> None:
    if client is not None and section.card_id:
        client.complete_section(section.card_id, section.key)


def _create(args: argparse.Namespace) -> int:
    paths = _paths()
    proposal = proposal_core.create_proposal(paths, args.slug, args.title, _section_specs(args.section))
    client = _kanban(args.slug)
    if client is not None:
        client.ensure_board()
        for section in proposal.sections:
            card_id = client.create_section(args.slug, section.key, section.title)
            proposal = proposal_core.bind_card(paths, args.slug, section.key, card_id)
    print(f"PROPOSAL-CREATED slug={proposal.slug} workspace={proposal.workspace} sections={len(proposal.sections)}")
    return 0


def _add_section(args: argparse.Namespace) -> int:
    paths = _paths()
    proposal = proposal_core.add_section(paths, args.slug, args.key, args.title)
    section = proposal_core.read_section(paths, args.slug, args.key)
    client = _kanban(args.slug)
    if client is not None:
        client.ensure_board()
        proposal_core.bind_card(paths, args.slug, section.key, client.create_section(args.slug, section.key, section.title))
    print(f"SECTION-ADDED slug={proposal.slug} section={args.key}")
    return 0


def _sections(args: argparse.Namespace) -> int:
    for section in proposal_core.list_sections(_paths(), args.slug):
        print(f"SECTION key={section.key} state={section.state.value} card={section.card_id or '-'}")
    return 0


def _rendering() -> Any:
    return proposal_knowledge.module("automation.knowledge.render")


def _evidence_block(pack: EvidencePack | proposal_knowledge._UnavailablePack) -> str:
    if pack.verdict != "hit":
        try:
            return str(_rendering().render_citations(pack, "sources"))
        except ImportError:
            return "EVIDENCE: unavailable — 근거 수집 불가"
    records = [
        f"[{item.id}] store={item.store}; ref={item.ref}; date={item.doc_date or '날짜 미상'}; content={item.content}"
        for item in pack.items
    ]
    return "EVIDENCE:\n" + "\n".join(records)


def _draft_prompt(
    paths: ProposalPaths, slug: str, key: str, brief: str,
    evidence_pack: EvidencePack | proposal_knowledge._UnavailablePack | None = None,
) -> str:
    section = proposal_core.read_section(paths, slug, key)
    evidence = "" if evidence_pack is None else f"\n\n{_evidence_block(evidence_pack)}"
    instruction = "" if evidence_pack is None else " Use only MATERIAL/EVIDENCE, cite [En], do not invent."
    return (
        f"Write only the Korean draft for proposal section '{section.title}'. "
        f"Use the supplied material, do not invent facts, and do not use tools.{instruction}\n\n"
        f"MATERIAL:\n{proposal_core.proposal_text(paths, slug)}\n\nSECTION BRIEF:\n{brief.strip()}{evidence}"
    )


def _finalize_evidence(content: str, pack: EvidencePack | proposal_knowledge._UnavailablePack) -> str:
    try:
        rendering = _rendering()
        report = rendering.validate_citations(content, pack)
        verdict = rendering.render_verdict(pack)
        citations = rendering.render_citations(pack, "footnotes")
        print(f"CITATIONS-STRIPPED count={len(report.stripped_ids)}", file=sys.stderr)
        parts = [part for part in (verdict, report.text, f"### 근거\n\n{citations}") if part]
        return "\n\n".join(parts)
    except ImportError:
        return f"근거 수집 불가 — 지식 파사드를 불러오지 못했지만 생성을 계속함\n\n{content.strip()}"


def _draft(
    args: argparse.Namespace,
    evidence_pack: EvidencePack | proposal_knowledge._UnavailablePack | None = None,
) -> int:
    paths = _paths()
    pack = evidence_pack
    if args.with_evidence and not args.brief_file:
        raise ProposalError("--with-evidence requires --brief-file")
    if args.file:
        content = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        content = args.text
    else:
        brief = Path(args.brief_file).read_text(encoding="utf-8")
        proposal = load_proposal(paths, args.slug)
        section = proposal_core.read_section(paths, args.slug, args.section)
        if args.with_evidence and pack is None:
            pack = proposal_knowledge.collect(section.title, brief, proposal.title)
        evidence_text = "\n".join(item.content for item in pack.items) if pack is not None else ""
        combined = "\n".join((proposal_core.proposal_text(paths, args.slug), brief, evidence_text))
        route = proposal_sensitivity.route_proposal(combined, proposal_sensitivity.load_rules(paths.rules_file))
        content = proposal_llm.run_section_draft(
            _draft_prompt(paths, args.slug, args.section, brief, pack), route.provider, route.model, route.sensitive
        )
        if pack is not None:
            content = _finalize_evidence(content, pack)
    proposal = proposal_core.write_draft(paths, args.slug, args.section, content, evidence_pack=pack)
    _complete_card(_kanban(args.slug), proposal_core.read_section(paths, args.slug, args.section))
    print(f"SECTION-DRAFTED slug={proposal.slug} section={args.section}")
    return 0


def _evidence(args: argparse.Namespace) -> int:
    brief = Path(args.brief_file).read_text(encoding="utf-8")
    goal = args.goal or next((line.strip() for line in brief.splitlines() if line.strip()), "")
    if not goal:
        raise ProposalError("evidence goal is empty")
    pack = proposal_knowledge.gather_owner_evidence(goal, section=args.section)
    if args.json:
        print(pack.to_json())
    else:
        buckets = ",".join(bucket for bucket, items in pack.by_bucket().items() if items) or "none"
        print(f"EVIDENCE count={len(pack.items)} buckets={buckets}")
        for note in pack.notes:
            print(note)
    return 0


def _contribute(args: argparse.Namespace) -> int:
    content = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    paths = _paths()
    proposal = proposal_core.fold_contribution(paths, args.slug, args.section, content, args.source)
    _complete_card(_kanban(args.slug), proposal_core.read_section(paths, args.slug, args.section))
    print(f"CONTRIBUTION-FOLDED slug={proposal.slug} section={args.section}")
    return 0


def _assemble(args: argparse.Namespace) -> int:
    companions = tuple(Path(value) for value in args.companion)
    for companion in companions:
        if not companion.is_file():
            print(f"COMPANION-MISSING {companion}", file=sys.stderr)
            return 1

    assembled = proposal_assembly.assemble(_paths(), args.slug)
    link = ""
    try:
        from automation.drive_outputs import publish_best_effort
    except ImportError:
        print("DRIVE-PUBLISH-SKIP reason=ImportError", file=sys.stderr)
    else:
        result = publish_best_effort(
            "proposal",
            args.slug,
            [(assembled.path, args.slug)],
            companions=companions,
        )
        if result is not None and result.links:
            link = result.links[0]
    missing = ",".join(assembled.missing_sections) or "none"
    print(f"PROPOSAL-ASSEMBLED path={assembled.path} drive={link} missing={missing}")
    if assembled.reminder:
        print(f"ASSEMBLY-REMINDER {assembled.reminder}")
    return 0


def _review_prompt(document: str) -> str:
    return (
        "Review this private Korean proposal exactly once. Return concise, actionable Korean review comments "
        "covering completeness, evidence gaps, risks, and next edits. Do not use tools or modify files.\n\n"
        f"ASSEMBLED PROPOSAL:\n{document}"
    )


def _review(args: argparse.Namespace) -> int:
    paths = _paths()
    assembled = paths.workspace_root / args.slug / "assembled.md"
    if not assembled.is_file():
        raise ProposalError("assemble the proposal before final review")
    target = proposal_dm.resolve_target(args.dm_target)
    document = assembled.read_text(encoding="utf-8")
    review = Path(args.response_file).read_text(encoding="utf-8") if args.response_file else proposal_llm.run_final_review(_review_prompt(document))
    path = proposal_assembly.append_final_review(paths, args.slug, review)
    proposal_dm.send_review(target, f"제안서 최종 검토 완료\n경로: {path}\n\n{review.strip()}")
    print(f"PROPOSAL-REVIEWED path={path} provider=openai-codex model=gpt-5.4")
    return 0


def _status(args: argparse.Namespace) -> int:
    import json

    from .proposal_version import VersionStore

    store = VersionStore.from_environment()
    slug_dir = store.resolve_slug_dir(args.slug)
    head_path = slug_dir / "HEAD"
    if not head_path.is_file() or head_path.is_symlink():
        print(f"PROPOSAL-STATUS-ERROR proposal slug is missing: {args.slug}", file=sys.stderr)
        return 1
    version = head_path.read_text(encoding="utf-8").strip()
    version_dir = slug_dir / "versions" / version
    if not version_dir.is_dir() or version_dir.is_symlink():
        raise ProposalError(f"proposal HEAD is invalid: {args.slug}")
    receipt = version_dir / "publish-receipt.json"
    state = "published" if receipt.is_file() and not receipt.is_symlink() else (
        "rendered" if (version_dir / "out" / "proposal.hwpx").is_file() else "staged"
    )
    payload = {"slug": args.slug, "state": state, "version": version}
    if args.json:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        print(f"PROPOSAL-STATUS slug={args.slug} state={state} version={version}")
    return 0


def _prompt_preview(args: argparse.Namespace) -> int:
    pack = proposal_knowledge.gather_owner_evidence(args.slug, section=args.section)
    print(proposal_prompts.assemble_section_prompt(args.section, pack))
    return 0


def _images(args: argparse.Namespace) -> int:
    argv = ["--slug", args.slug]
    if args.json:
        argv.append("--json")
    return proposal_images.main(argv)


def _preflight(args: argparse.Namespace) -> int:
    argv: list[str] = []
    for name in args.require:
        argv.extend(("--require", name))
    if args.json:
        argv.append("--json")
    if args.stage:
        argv.extend(("--stage", args.stage))
    return proposal_preflight.main(argv)


def _version(args: argparse.Namespace) -> int:
    from .proposal_improve_cmd import version_command

    return version_command(args)


def _improve(args: argparse.Namespace) -> int:
    from .proposal_improve_cmd import improve_command

    return improve_command(args)


def _delta(args: argparse.Namespace) -> int:
    import hashlib
    import importlib
    import json
    import shutil
    import tempfile

    from . import proposal_version

    try:
        delta_module = importlib.import_module(".proposal_delta", package=__package__)
        store = proposal_version.VersionStore.from_environment()
        slug_root = store.resolve_slug_dir(args.slug)
        store.head(args.slug)
        scratch = Path(tempfile.mkdtemp(prefix=".delta-", dir=slug_root / "staging"))
        report = delta_module.collect_deltas(
            args.slug, since_version=args.since, dest_dir=scratch
        )
        run_material = json.dumps(
            sorted(item.sha256 for item in report.collected), separators=(",", ":")
        ).encode("utf-8")
        run_key = hashlib.sha256(run_material).hexdigest()
        staged = store.begin(args.slug, run_key)
        if isinstance(staged, proposal_version.Reused):
            shutil.rmtree(scratch)
            destination = staged.path
        else:
            target = staged.path / "delta"
            if target.exists():
                shutil.rmtree(target)
            os.replace(scratch / "delta", target)
            scratch.rmdir()
            destination = staged.path
        payload = report.payload()
        payload["destination"] = str(destination)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(
                f"PROPOSAL-DELTA collected={report.collected_count} "
                f"counts={report.counts} sha256={payload['sha256']}"
            )
            for skip in report.skipped:
                print(f"DELTA-SKIP source={skip.source_key} reason={skip.reason}")
        return 0
    except (RuntimeError, OSError) as error:
        print(f"PROPOSAL-DELTA-ERROR {error}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proposal")
    commands = parser.add_subparsers(dest="command", required=True)
    version = commands.add_parser("version")
    version.add_argument("--slug", required=True)
    version.add_argument("--json", action="store_true")
    version.set_defaults(func=_version)
    improve = commands.add_parser("improve")
    improve.add_argument("--slug", required=True)
    improve.add_argument("--since", required=True)
    improve.add_argument("--resolve", action="append", default=[])
    improve.add_argument("--profile", choices=("30-page", "10-page"))
    improve.add_argument("--json", action="store_true")
    improve.set_defaults(func=_improve)
    delta = commands.add_parser("delta")
    delta.add_argument("--slug", required=True)
    delta.add_argument("--since", required=True)
    delta.add_argument("--json", action="store_true")
    delta.set_defaults(func=_delta)
    create = commands.add_parser("create")
    create.add_argument("--slug", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--section", action="append", required=True)
    create.set_defaults(func=_create)
    add = commands.add_parser("section-add")
    add.add_argument("--slug", required=True)
    add.add_argument("--key", required=True)
    add.add_argument("--title", required=True)
    add.set_defaults(func=_add_section)
    sections = commands.add_parser("sections")
    sections.add_argument("--slug", required=True)
    sections.set_defaults(func=_sections)
    draft = commands.add_parser("draft")
    draft.add_argument("--slug", required=True)
    draft.add_argument("--section", required=True)
    source = draft.add_mutually_exclusive_group(required=True)
    source.add_argument("--file")
    source.add_argument("--text")
    source.add_argument("--brief-file")
    draft.add_argument("--with-evidence", action="store_true")
    draft.set_defaults(func=_draft)
    evidence = commands.add_parser("evidence")
    evidence.add_argument("--slug", required=True)
    evidence.add_argument("--section", required=True)
    evidence.add_argument("--brief-file", required=True)
    evidence.add_argument("--goal")
    evidence.add_argument("--json", action="store_true")
    evidence.set_defaults(func=_evidence)
    research = commands.add_parser("research")
    research.add_argument("--slug", required=True)
    research.add_argument("--goal")
    research.add_argument("--validate-only", action="store_true")
    research.add_argument("--json", action="store_true")
    research.set_defaults(func=research_command)
    corpus = commands.add_parser("corpus")
    corpus.add_argument("--slug", required=True)
    corpus.add_argument("--json", action="store_true")
    corpus.set_defaults(func=corpus_command)
    contribute = commands.add_parser("contribute")
    contribute.add_argument("--slug", required=True)
    contribute.add_argument("--section", required=True)
    contribute.add_argument("--source", required=True)
    source = contribute.add_mutually_exclusive_group(required=True)
    source.add_argument("--file")
    source.add_argument("--text")
    contribute.set_defaults(func=_contribute)
    assemble = commands.add_parser("assemble")
    assemble.add_argument("--slug", required=True)
    assemble.add_argument("--companion", action="append", default=[])
    assemble.set_defaults(func=_assemble)
    review = commands.add_parser("review")
    review.add_argument("--slug", required=True)
    review.add_argument("--dm-target", default="")
    review.add_argument("--response-file", default="")
    review.set_defaults(func=_review)
    status = commands.add_parser(
        "status", help="report staged, rendered, or published HEAD state (receipt marks published)"
    )
    status.add_argument("--slug", required=True)
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=_status)
    prompt_preview = commands.add_parser("prompt-preview")
    prompt_preview.add_argument("--slug", required=True)
    prompt_preview.add_argument("--section", required=True)
    prompt_preview.set_defaults(func=_prompt_preview)
    images = commands.add_parser("images")
    images.add_argument("--slug", required=True)
    images.add_argument("--json", action="store_true")
    images.set_defaults(func=_images)
    from .proposal_render import command as render_command

    render = commands.add_parser("render")
    render.add_argument("--slug", required=True)
    render.add_argument("--mode", choices=("replay", "live"), default="replay")
    render.add_argument("--profile", choices=("30-page", "10-page"))
    render.add_argument("--allow-missing-figures", action="store_true")
    render.add_argument("--json", action="store_true")
    render.set_defaults(func=render_command)
    from .proposal_visual_review import command as visual_review_command

    visual_review = commands.add_parser(
        "visual-review",
        help="render the current HWPX into page images for direct visual inspection",
    )
    visual_review.add_argument("--slug", required=True)
    visual_review.add_argument("--json", action="store_true")
    visual_review.set_defaults(func=visual_review_command)
    from .proposal_refine import command as refine_command

    refine = commands.add_parser("refine")
    refine.add_argument("--slug", required=True)
    refine.add_argument("--json", action="store_true")
    refine.set_defaults(func=refine_command)
    from .proposal_publish import command as publish_command

    publish = commands.add_parser("publish")
    publish.add_argument("--slug", required=True)
    publish.add_argument("--version", required=True)
    publish.add_argument("--json", action="store_true")
    publish.set_defaults(func=publish_command)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--json", action="store_true")
    preflight.add_argument("--require", action="append", default=[])
    preflight.add_argument("--stage")
    preflight.set_defaults(func=_preflight)
    return parser


_MUTATING_COMMANDS = frozenset({
    "create", "section-add", "draft", "contribute", "assemble", "review",
    "delta", "improve", "refine", "publish",
})


def main(argv: list[str] | None = None) -> int:
    proposal_env.load_env_secrets()
    args = build_parser().parse_args(argv)
    if args.command in _MUTATING_COMMANDS:
        message = proposal_governed.refusal(Path(__file__).resolve())
        if message:
            print(message, file=sys.stderr)
            return 3
    try:
        return int(args.func(args))
    except (
        OSError,
        ProposalError,
        proposal_dm.DeliveryError,
        proposal_kanban.KanbanError,
        proposal_llm.LlmInvocationError,
    ) as error:
        print(f"PROPOSAL-ERROR {error.__class__.__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
