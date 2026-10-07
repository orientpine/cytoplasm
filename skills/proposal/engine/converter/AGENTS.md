# MODULE: converter

Corpus → evidence units → queryable store. The pipeline's front half + the leak gate.

## FILES
| File | Role |
|------|------|
| `ingest.py` | files → `RawDoc`. `ingest_dir`. `SUPPORTED_EXTENSIONS` = .md/.txt/.eml/.docx/.pdf/.hwpx/.json (docx/pdf via lazy `import_module`). `ingest_md` parses leading `---\nsource_url: ...\n---` front-matter into `meta["source_url"]` (stripped from `raw_text`). |
| `normalize.py` | `RawDoc` → `DocRecord`. Regex-detects PII (Korean name/email/phone) + NDA/contract/KRW → sets `sensitivity_flag`. |
| `materialize.py` | `DocRecord` → `EvidenceUnit[]`. Segments text, classifies into buckets via `BUCKET_KEYWORDS`, dedups, picks max sensitivity. |
| `research_convert.py` | ultraresearch `SYNTHESIS.md` → per-URL `research-<sha8>.md` corpus files. CONFIRMED claims only, atomic, deterministic. Requires Detailed Findings + External Sources + Verified Claims sections else exit 1. |
| `corpus_lint.py` | Pre-ingest lint: runs ingest→normalize→materialize on corpus + candidates, predicts PUBLIC/INTERNAL/REDACT per unit + conflict detection. Exit 1 on violation unless `--warn-only`. |
| `pms.py` | `ProposalMaterialStore`: indexes units by bucket/id, holds citation ledger. |
| `sanitize.py` | `sanitize_gate`: second leak barrier (verbatim/chunk/NDA-marker/contract-value scan). |
| `excavator_convert.py` | `convert_excavator_corpus()` / `main()` — KIMM excavator wiki dump → corpus files. Runnable via `python -m kimm_docbot.converter.excavator_convert <wiki-dir> <out-dir>` (used by `make validate-excavator`). |
| `traceability_graph.py` | `build_evidence_graph(pms, drafts)` → `EvidenceGraph`. Deterministic claim→evidence→source graph, PUBLIC units only, fully sorted. Feeds render's [B] traceability appendix + `coverage_score`. |

## SENSITIVITY → CITATION (pms.py `SENSITIVITY_TO_CITATION_STATUS`)
`PII→REDACT`, `IP→INTERNAL`, `NDA→INTERNAL`, `NONE→PUBLIC`. Only PUBLIC is emitted.

## CONVENTIONS / GOTCHAS
- Sensitivity detection is regex-driven — adding a leak class means updating BOTH `normalize.py` patterns AND `sanitize.py` markers.
- `ProposalMaterialStore._by_bucket` / `_by_id` / `_ledger` are private but accessed externally (orchestrator, sanitize) — keep their shape stable.
- `sanitize._MIN_CHUNK = 20`: leak detection is substring-based at ≥20 chars; do not lower silently.
- Bucket names here are Korean (배경/필요성, 목표/KPI후보, …) and differ from planner's English bucket keys — `planner.BUCKET_ALIASES` bridges them.
