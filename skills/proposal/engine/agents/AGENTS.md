# MODULE: agents

LLM-driven drafting + self-critique + revision. All LLM access goes through the injectable `LLMClient` protocol — real API 호출은 `--mode record` 시 `CachingLLMClient`를 통해 수행됩니다.

## FILES
| File | Role |
|------|------|
| `llm.py` | `MockLLMClient`: responses keyed by `(role, sha256(prompt))`. `load_mock_responses` reads `tests/fixtures/llm/responses.json`. `_record_buffer` captures unseen prompts for fixture authoring. Also `RealLLMClient` (env-driven stub) + `get_llm(config)` factory — real path: `--mode record/replay` 경유 `CachingLLMClient` 주입. |
| `writers.py` | `write_all` → 5 `SectionDraft`s. Fixed dispatch: 0 summary · 1 background · 2 objectives · 3 methods · 4 impact. |
| `critic.py` | `critique` → `CriticReport`. `RUBRIC_CRITERIA` = citation_cover, kpi_sum, sections, trl_monotonic, keyword_count, no_orphan_claims, no_conflict_evidence. `llm.complete("critic",...)` — real 파이프라인(`CachingLLMClient`) 시 LLM 활성화. |
| `reviser.py` | `revise`: patches rejections by cell — `ir.*` cells fix the plan, `secN.claimM` cells fix drafts. |
| `style_lint.py` | `lint` + `compress` to char budget. `_lint_da_endings` enforces **보고서체 `-다` 종결** and FLAGS polite `습니다`/`입니다`/`합니다` as `non_da_ending` violations (the OPPOSITE of polite form). `check_forbidden_expressions` scans `KIMM_DOMAIN.forbidden_expressions`. |
| `judge.py` | `judge_proposal(text, llm, threshold=3)` → `JudgeResult`. 7 `JUDGE_CRITERIA` × `JUDGE_WEIGHTS` (20/20/20/10/10/10/10=100) → 100-pt; missing-section cap (2→60, 3+→40); defensive parse → default 3. **OPT-IN, non-deterministic, NOT in CI**; powers `kimm-docbot judge`. |
| `rubric_check.py` | `check_rubric(drafts, kpis, cover)` → `RubricFinding`s for the owner evaluator's deductions (KPI_VALUE_DRIFT · KPI_PROTOCOL_MISSING · KPI_RESTATED · NUMBER_RESTATED · SCHEDULE_RESTATED · TERM_VARIANT). Deterministic, never rewrites text; `render` writes them to `<out>.quality.json`. `normalize_spellings` applies `kimm_domain.STANDARD_SPELLINGS` outside evidence spans. |
| `kimm_domain.py` | `KIMM_DOMAIN: KIMMDomainPack` = single immutable domain config: `SECTION_REQUIREMENTS`/`SECTION_TITLES`, `ALLOWED_ENDINGS`/`FORBIDDEN_ENDINGS`, `FORBIDDEN_EXPRESSIONS`, `QUANTITATIVE_REQUIRED_SECTIONS`, `KPISchema`/`TRLRequirement`/`BudgetSchema`. Consumed by `style_lint` + orchestrator [C] checks. |

## GOTCHAS
- Section count is hard-wired to 5 (`range(5)` / writer dispatch) — matches rule.md mandatory sections; changing it touches anchor_map + validators too.
- `MockLLMClient.complete` raises `KeyError` on an unknown `(role, prompt-hash)` unless recording — a new/changed prompt means regenerating the fixture.
- Rubric criterion names in `critic.py` must stay aligned with `contracts/validators.py` checks and `reviser.py` cell parsing.
- Draft bodies must be final prose — **no placeholder tokens** (`TODO`, `{{`, `}}`, `__`); `tests/integration/test_writers_to_renderer.py` fails the build if any leak through.
- Style endings are **declarative `-다` (보고서체), NOT polite `습니다`** — `_POLITE_ENDINGS` are violations; never "correct" drafts to polite form. `kimm_domain.FORBIDDEN_ENDINGS` mirrors this list.
- `judge.py` is opt-in eval only — never wire it into the deterministic pipeline or a CI assertion (it is non-deterministic; use `CachingLLMClient` record→replay for reproducible runs).
