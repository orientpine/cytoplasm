# PACKAGE: kimm_docbot

Pipeline that turns an evidence corpus into a KIMM R&D 연구계획서 HWPX. Single-pass, live by default (replay optional). (Root conventions/anti-patterns apply — see `../../AGENTS.md`.)

## PIPELINE (13-node linear DAG, `pipeline/orchestrator.py:run`)
```
ingest → normalize → materialize → pms → planner → writers
  → style_lint → critic → reviser → render
  → emit_citation_sidecar → sanitize_gate → validate_public_artifact
```
Order is fixed in `NODE_NAMES`; each step appends a `NodeLog` (input/output `stable_id` hash). To add a stage: insert into `run()` AND `NODE_NAMES`, keep both in sync.

## MODULE MAP (owner of each phase)
| Dir | Role | Key entry |
|-----|------|-----------|
| `converter/` | corpus → evidence units + store | `ingest_dir`, `normalize`, `materialize`, `ProposalMaterialStore`, `sanitize_gate`, `research_convert`, `corpus_lint`, `build_evidence_graph` (traceability), `excavator_convert` |
| `contract/` | evidence → `PlanSpec` (the plan) | `planner.plan` |
| `agents/` | `PlanSpec` → section prose, critique, revise, judge | `write_all`, `critique`, `revise`, `judge_proposal`, `KIMM_DOMAIN`, `MockLLMClient` |
| `hwpx/` | drafts → HWPX bytes + final validation | `set_text`, `write_kpi_table`, `write_gantt`, `repack`, `validate_public_artifact` |
| `contracts/` | shared frozen models + protocols + validators | `models`, `protocols`, `validators`, `ids` |
| `pipeline/` | CLI + orchestration + repro harness | `cli.main`, `orchestrator.run`, `harness` |

## DATA FLOW (all frozen pydantic models, `contracts/models.py`)
`RawDoc → DocRecord → EvidenceUnit → (PMS) → PlanSpec → SectionDraft[] → HWPX`
Citation verification rides along: `CitationStatus` in PMS depends only on optional source cross-reference verification; document words do not change eligibility.
At render, a deterministic `EvidenceGraph` (claim→evidence→source, PUBLIC-only) is built via `converter/traceability_graph.build_evidence_graph` → traceability appendix + `coverage_score`.

## GOTCHAS
- `contract/` (planner) vs `contracts/` (models) — different packages, easy to mis-import.
- `_render`/`_gantt_schedule` in orchestrator own HWPX assembly order; tables written after section text.
- Reviser is best-effort: it swallows `AssertionError` and keeps original drafts (`_revise_or_keep`).
- [A] critic/reviser loop, [B] evidence graph, [C] `kimm_domain` checks integrate INTO existing nodes (writers/style_lint/critic/reviser/render) — node count stays 13; [C] checks are **warn-only**.
- `kimm-docbot judge` (`agents/judge.py`) is a **separate opt-in CLI**, NOT a pipeline node — non-deterministic, excluded from `make check`.
