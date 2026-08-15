from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from automation.memory_curator import classify as classify_module
from automation.memory_curator.classify import classify_entries
from automation.memory_curator.classify_model import EntryVerdict, Route
from automation.memory_curator.classify_prompt import render
from automation.memory_curator.model import MemoryEntry, MemoryKind
from automation.rag_ingest.sensitivity import SensitivityRule, load_rules
from automation.twin_distill.llm import LlmInvocationError

_OPS_ENTRY = (
    "The curator state database is stored at "
    "/srv/autophagy-private/memory-curator/state.sqlite and the scheduled worker "
    "reads that exact path at startup."
)
_OPS_EVIDENCE = "/srv/autophagy-private/memory-curator/state.sqlite"


@dataclass(frozen=True, slots=True)
class FakeLlm:
    """Record prompts and consume prepared responses in insertion order."""

    responses: list[str]
    prompts: list[str]

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


@dataclass(frozen=True, slots=True)
class RaisingLlm:
    prompts: list[str]

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        raise LlmInvocationError("boom")


def _rules() -> tuple[SensitivityRule, ...]:
    return load_rules(Path("configs/sensitivity-rules.yaml"))


def _response(route: Route, evidence: str) -> str:
    return json.dumps(
        {"route": route, "evidence": evidence, "reason": "grounded classification"}
    )


def test_pre_llm_veto_keeps_sensitive_text_out_of_the_client() -> None:
    # Given one sensitive entry followed by one LLM-eligible entry
    sensitive = "특허 출원 전 검토할 발명 설명"
    client = FakeLlm(
        responses=[_response("OPS_REFERENCE", _OPS_EVIDENCE)], prompts=[]
    )
    entries: Mapping[MemoryKind, tuple[MemoryEntry, ...]] = {
        "memory": (MemoryEntry(sensitive), MemoryEntry(_OPS_ENTRY)),
        "user": (),
    }

    # When the batch is classified
    verdicts = classify_entries(entries, client=client, rules=_rules())

    # Then the vetoed text never reaches the LLM and only the eligible entry does.
    assert verdicts[0].veto == "sensitivity"
    assert verdicts[0].llm_called is False
    assert client.prompts == [render(_OPS_ENTRY, source_kind="memory")]
    assert len(client.prompts) == 1


def test_llm_error_closes_each_entry_and_continues_in_order() -> None:
    # Given two eligible entries and a client that fails on every call
    second = (
        "The curator batch journal is stored at /var/lib/autophagy/curator/journal.jsonl "
        "and each scheduled pass appends one durable record after startup."
    )
    client = RaisingLlm(prompts=[])
    entries: Mapping[MemoryKind, tuple[MemoryEntry, ...]] = {
        "memory": (MemoryEntry(_OPS_ENTRY), MemoryEntry(second)),
        "user": (),
    }

    # When the batch is classified
    verdicts = classify_entries(entries, client=client, rules=_rules())

    # Then both failures are isolated and both entries remain in input order.
    assert verdicts == (
        EntryVerdict(
            "memory", _OPS_ENTRY, "UNCERTAIN", "", "", "llm_error", True
        ),
        EntryVerdict("memory", second, "UNCERTAIN", "", "", "llm_error", True),
    )
    assert len(client.prompts) == 2


def test_user_ops_reference_is_overridden_after_llm() -> None:
    # Given an eligible USER.md entry classified as an operations reference
    client = FakeLlm(
        responses=[_response("OPS_REFERENCE", _OPS_EVIDENCE)], prompts=[]
    )
    entries: Mapping[MemoryKind, tuple[MemoryEntry, ...]] = {
        "memory": (),
        "user": (MemoryEntry(_OPS_ENTRY),),
    }

    # When classification completes
    (verdict,) = classify_entries(entries, client=client, rules=_rules())

    # Then the V7 post-LLM rule keeps USER.md content native.
    assert verdict.route == "KEEP_NATIVE"
    assert verdict.veto == "user_file"
    assert verdict.llm_called is True


def test_safety_cue_is_overridden_after_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a post-LLM safety defense and an earlier gate that missed the cue
    entry = (
        "프로덕션 승인 요구사항은 네이티브 파일에 유지하며 curator는 각 scheduled pass의 "
        "supporting operational context를 별도 레코드로 남긴다."
    )
    evidence = "프로덕션 승인 요구사항"

    def allow_llm(
        _text: str,
        *,
        source_kind: MemoryKind,
        rules: Sequence[SensitivityRule],
    ) -> None:
        del source_kind, rules

    monkeypatch.setattr(classify_module, "pre_llm_veto", allow_llm)
    client = FakeLlm(responses=[_response("OPS_REFERENCE", evidence)], prompts=[])
    entries: Mapping[MemoryKind, tuple[MemoryEntry, ...]] = {
        "memory": (MemoryEntry(entry),),
        "user": (),
    }

    # When the LLM verdict passes through the independent post-veto defense
    (verdict,) = classify_entries(entries, client=client, rules=_rules())

    # Then V6 keeps the safety rule native.
    assert verdict.route == "KEEP_NATIVE"
    assert verdict.veto == "keep_native_rule"
    assert verdict.llm_called is True


def test_output_order_is_memory_then_user_and_stable_within_each_kind() -> None:
    # Given a mapping inserted in the opposite order from the API contract
    memory_entries = tuple(
        MemoryEntry(f"{_OPS_ENTRY} Memory sequence index {index} is persisted here.")
        for index in range(2)
    )
    user_entries = tuple(
        MemoryEntry(f"{_OPS_ENTRY} User sequence index {index} is persisted here.")
        for index in range(2)
    )
    entries: Mapping[MemoryKind, tuple[MemoryEntry, ...]] = {
        "user": user_entries,
        "memory": memory_entries,
    }
    client = FakeLlm(responses=["not-json"] * 4, prompts=[])

    # When the mapping is classified
    verdicts = classify_entries(entries, client=client, rules=_rules())

    # Then kind priority and each input tuple's order are deterministic.
    assert tuple((item.source_kind, item.entry_text) for item in verdicts) == (
        ("memory", memory_entries[0].text),
        ("memory", memory_entries[1].text),
        ("user", user_entries[0].text),
        ("user", user_entries[1].text),
    )


def test_memory_ops_fact_stays_ops_reference_through_the_full_pipeline() -> None:
    # Given a grounded operations fact with no pre-LLM veto cue
    client = FakeLlm(
        responses=[_response("OPS_REFERENCE", _OPS_EVIDENCE)], prompts=[]
    )
    entries: Mapping[MemoryKind, tuple[MemoryEntry, ...]] = {
        "memory": (MemoryEntry(_OPS_ENTRY),),
        "user": (),
    }

    # When the entry flows through render, complete, parse, and post-veto
    (verdict,) = classify_entries(entries, client=client, rules=_rules())

    # Then the grounded LLM verdict remains unchanged.
    assert verdict == EntryVerdict(
        "memory",
        _OPS_ENTRY,
        "OPS_REFERENCE",
        _OPS_EVIDENCE,
        "grounded classification",
        None,
        True,
    )
    assert client.prompts == [render(_OPS_ENTRY, source_kind="memory")]
