#!/usr/bin/env python3
"""Private command surface for the personal proposal drafting workspace."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from automation.knowledge.pack import EvidencePack


_SCRIPT_DIR = Path(__file__).absolute().parent
if __package__ in (None, ""):
    sys.path.insert(0, str(_SCRIPT_DIR.parents[1]))
    __package__ = "proposal.scripts"

from . import drive_publish, proposal_assembly, proposal_core, proposal_dm, proposal_kanban, proposal_knowledge, proposal_llm, proposal_sensitivity  # noqa: E402
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
    paths = _paths()
    proposal = load_proposal(paths, args.slug)
    section = proposal_core.read_section(paths, args.slug, args.section)
    brief = Path(args.brief_file).read_text(encoding="utf-8")
    pack = proposal_knowledge.collect(section.title, brief, proposal.title)
    if args.json:
        print(json.dumps({"evidence_count": len(pack.items), "layers": pack.layers}, ensure_ascii=False, sort_keys=True))
    else:
        print(f"EVIDENCE verdict={pack.verdict} count={len(pack.items)}")
        try:
            print(_rendering().render_citations(pack, "sources"))
        except ImportError:
            print("근거 수집 불가")
    return 0


def _contribute(args: argparse.Namespace) -> int:
    content = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    paths = _paths()
    proposal = proposal_core.fold_contribution(paths, args.slug, args.section, content, args.source)
    _complete_card(_kanban(args.slug), proposal_core.read_section(paths, args.slug, args.section))
    print(f"CONTRIBUTION-FOLDED slug={proposal.slug} section={args.section}")
    return 0


def _assemble(args: argparse.Namespace) -> int:
    assembled = proposal_assembly.assemble(_paths(), args.slug)
    link = drive_publish.publish_best_effort(assembled.path, "proposal")
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
    proposal = load_proposal(_paths(), args.slug)
    print(proposal.status_path.read_text(encoding="utf-8"), end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proposal")
    commands = parser.add_subparsers(dest="command", required=True)
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
    evidence.add_argument("--json", action="store_true")
    evidence.set_defaults(func=_evidence)
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
    assemble.set_defaults(func=_assemble)
    review = commands.add_parser("review")
    review.add_argument("--slug", required=True)
    review.add_argument("--dm-target", default="")
    review.add_argument("--response-file", default="")
    review.set_defaults(func=_review)
    status = commands.add_parser("status")
    status.add_argument("--slug", required=True)
    status.set_defaults(func=_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
