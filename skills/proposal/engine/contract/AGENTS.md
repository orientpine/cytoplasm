# MODULE: contract (singular)

Evidence → `PlanSpec` synthesis. ONE public entry: `plan(pms, llm) -> PlanSpec`. NOT `contracts/` plural (= the frozen models it consumes). Biggest single file in the repo.

## FILE
| File | Role |
|------|------|
| `planner.py` (538) | `plan`: pulls `pms.public_evidence()`, buckets it, asks the LLM only for *phrasing* (title/objectives/keywords), then builds KPIs/work-packages/TRL/traceability **deterministically from regex over evidence**. `InsufficientEvidenceError` raised when grounding is missing. |

## HOW `plan` WORKS (grounding-first, LLM-thin)
1. `public_evidence` sorted by `unit_id` — empty → `InsufficientEvidenceError`.
2. `_evidence_for_bucket` maps units into 5 `MANDATORY_BUCKETS` (objectives/kpis/methodology/schedule/collaboration) via `BUCKET_ALIASES` (PMS bucket names, KR+EN) + `BUCKET_TOKENS` (substring fallback on normalized bucket text).
3. LLM (`llm.complete("planner", ...)`) returns ONLY title/objectives/keywords; everything numeric is parsed from evidence, never invented.
4. `_planner_phrasing` drops any phrase whose numbers are not in the evidence (`_numeric_grounded`) — LLM cannot smuggle new figures.
5. KPIs/WPs/TRL built by regex (`_build_kpis`, `_build_work_packages`, `_trl_range`); each raises `InsufficientEvidenceError` if no grounded baseline/target/weight (KPI) or duration (WP).
6. KPI weights normalized via `normalize_kpi_weights`; `keywords` capped at 5.

## GOTCHAS
- **`BUCKET_ALIASES` bridges Korean PMS bucket names ↔ English planner keys** — converter emits KR buckets (배경/필요성, 목표/KPI후보…); keep both alias lists in sync when bucket names change.
- LLM output is phrasing-only and ungrounded-number-stripped; missing/empty LLM response falls back to `_*_from_evidence` helpers — the pipeline still produces a valid plan (a fixture path tests this).
- `_trl_range` defaults to `(3, 6)` only when no `trl_start<trl_end` evidence is found; the empty trace-unit list is intentional.
- Constructing `PlanSpec`/`IR` re-runs `validators` (KPI sum, `trl_start<trl_end`) — a bad regex extraction fails loudly at construction, not silently.
- All numeric parsing is regex (`NUMBER_RE`, `TRL_RANGE_RE`, `MONTH_RE`, …); adding a new evidence shape means a new regex + grounding check, not a new LLM prompt.
