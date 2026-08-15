"""On-demand inferred distillation entrypoint that creates a wiki draft only."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from automation.twin_distill.gather import (
    DistillationContext,
    EvidenceExcerpt,
    EvidenceMetadata,
    GatherRequest,
    NoEligibleEvidenceError,
    RecallSearchClient,
    RecallSearchResult,
    gather_context,
    render_prompt,
)
from automation.twin_distill.llm import (
    LiteLlmClient,
    LlmClient,
    LlmConfigurationError,
    LlmInvocationError,
)
from automation.twin_distill.validate import (
    AuthorityCapError,
    CandidateMetaError,
    CandidateSpec,
    CandidateValidationError,
    InferredProposal,
    build_proposal,
)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class DraftEmission:
    command: tuple[str, ...]
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class DraftEmissionError(Exception):
    returncode: int

    def __str__(self) -> str:
        return f"wiki draft subprocess failed with exit code {self.returncode}"


@dataclass(frozen=True, slots=True)
class InvocationInputError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


class ProcessRunner(Protocol):
    def run(self, command: tuple[str, ...], *, stdin: str, env: Mapping[str, str]) -> ProcessResult: ...


class DraftRunner(Protocol):
    def emit(self, proposal: InferredProposal) -> DraftEmission: ...


@dataclass(frozen=True, slots=True)
class PythonProcessRunner:
    def run(self, command: tuple[str, ...], *, stdin: str, env: Mapping[str, str]) -> ProcessResult:
        completed = subprocess.run(
            command,
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            env=dict(env),
        )
        return ProcessResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True, slots=True)
class WikiDraftRunner:
    wiki_cli_path: Path
    environment: Mapping[str, str]
    process_runner: ProcessRunner

    def emit(self, proposal: InferredProposal) -> DraftEmission:
        command = (
            sys.executable,
            str(self.wiki_cli_path),
            "draft",
            "--title",
            proposal.title,
            "--kind",
            proposal.kind,
            "--authority",
            proposal.authority.value,
            "--provenance",
            proposal.provenance,
            "--channel-id",
            "dm",
            "--stdin",
        )
        if proposal.tags:
            command = command[:-1] + ("--tags", ",".join(proposal.tags), "--stdin")
        result = self.process_runner.run(command, stdin=proposal.body, env=self.environment)
        if result.returncode != 0:
            raise DraftEmissionError(result.returncode)
        return DraftEmission(command, result.stdout, result.stderr)


@dataclass(frozen=True, slots=True)
class DistillationInvocation:
    gather_request: GatherRequest
    title: str
    authority: str
    kind: str = "principle"
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DistillationDependencies:
    search_client: RecallSearchClient
    llm: LlmClient
    draft_runner: DraftRunner


@dataclass(frozen=True, slots=True)
class LiveDependencies:
    search_client: RecallSearchClient
    draft_runner: DraftRunner
    environment: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class StaticSearchClient:
    results: tuple[RecallSearchResult, ...]

    def search(self, query: str) -> tuple[RecallSearchResult, ...]:
        del query
        return self.results


def run_distillation(
    invocation: DistillationInvocation,
    dependencies: DistillationDependencies,
) -> DraftEmission:
    context: DistillationContext = gather_context(invocation.gather_request, dependencies.search_client)
    candidate_body = dependencies.llm.complete(render_prompt(context))
    proposal = build_proposal(
        CandidateSpec(
            title=invocation.title,
            authority=invocation.authority,
            kind=invocation.kind,
            tags=invocation.tags,
        ),
        candidate_body,
    )
    return dependencies.draft_runner.emit(proposal)


def run_with_live_llm(
    invocation: DistillationInvocation,
    dependencies: LiveDependencies,
) -> DraftEmission:
    llm = LiteLlmClient.from_environment(dependencies.environment)
    return run_distillation(
        invocation,
        DistillationDependencies(dependencies.search_client, llm, dependencies.draft_runner),
    )


def _load_rows(path: Path) -> tuple[EvidenceExcerpt, ...]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InvocationInputError(f"cannot read evidence JSON: {error.__class__.__name__}") from None
    if isinstance(decoded, dict):
        decoded = decoded.get("results")
    if not isinstance(decoded, list):
        raise InvocationInputError("evidence JSON must be a list or an object with a results list")
    rows: list[EvidenceExcerpt] = []
    for row in decoded:
        if not isinstance(row, dict):
            raise InvocationInputError("evidence row must be an object")
        source = row.get("source", row.get("source_key"))
        content = row.get("content", row.get("excerpt"))
        metadata = row.get("metadata", {})
        if not isinstance(source, str) or not source.strip():
            raise InvocationInputError("evidence row source must be a non-empty string")
        if not isinstance(content, str) or not content.strip():
            raise InvocationInputError("evidence row content must be a non-empty string")
        if not isinstance(metadata, dict):
            raise InvocationInputError("evidence row metadata must be an object")
        sensitivity = metadata.get("sensitivity")
        source_type = metadata.get("source_type", "")
        if sensitivity is not None and not isinstance(sensitivity, str):
            raise InvocationInputError("evidence sensitivity must be a string")
        if not isinstance(source_type, str):
            raise InvocationInputError("evidence source_type must be a string")
        rows.append(EvidenceExcerpt(source, content, EvidenceMetadata(sensitivity, source_type)))
    return tuple(rows)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="twin-distill", description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--authority", default="default")
    parser.add_argument("--kind", default="principle")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--search-results", type=Path, required=True)
    parser.add_argument("--conversation-excerpts", type=Path)
    parser.add_argument("--meeting-excerpts", type=Path)
    return parser.parse_args(argv)


def _invocation_from_args(args: argparse.Namespace) -> tuple[DistillationInvocation, StaticSearchClient]:
    recalled = _load_rows(args.search_results)
    conversations = _load_rows(args.conversation_excerpts) if args.conversation_excerpts else ()
    meetings = _load_rows(args.meeting_excerpts) if args.meeting_excerpts else ()
    invocation = DistillationInvocation(
        gather_request=GatherRequest(
            query=args.query,
            conversation_excerpts=conversations,
            meeting_excerpts=meetings,
        ),
        title=args.title,
        authority=args.authority,
        kind=args.kind,
        tags=tuple(args.tag),
    )
    results = tuple(RecallSearchResult(item.source_key, item.content, item.metadata) for item in recalled)
    return invocation, StaticSearchClient(results)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        invocation, search_client = _invocation_from_args(args)
        environment = dict(os.environ)
        runner = WikiDraftRunner(
            wiki_cli_path=Path(__file__).resolve().parents[2] / "skills" / "wiki" / "scripts" / "wiki_cli.py",
            environment=environment,
            process_runner=PythonProcessRunner(),
        )
        emission = run_with_live_llm(
            invocation,
            LiveDependencies(search_client, runner, environment),
        )
    except (
        AuthorityCapError,
        CandidateMetaError,
        CandidateValidationError,
        DraftEmissionError,
        InvocationInputError,
        LlmConfigurationError,
        LlmInvocationError,
        NoEligibleEvidenceError,
    ) as error:
        print(f"TWIN-DISTILL-REFUSED {error}", file=sys.stderr)
        return 1
    print(emission.stdout, end="" if emission.stdout.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
