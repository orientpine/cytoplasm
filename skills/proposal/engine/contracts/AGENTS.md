# MODULE: contracts (plural)

Shared data contracts: frozen pydantic models, cross-module protocols, validators, id hashing. The type backbone every other module imports. (NOT `contract/` singular = the planner.)

## FILES
| File | Role |
|------|------|
| `models.py` | All domain models. `FrozenModel` base = `ConfigDict(frozen=True)` → immutable. Enums `SensitivityFlag`, `CitationStatus`. `IR`→`PlanSpec` inheritance. DocRecord/Provenance carry additive `source_url: str | None = None` (research origin URL; backward-compatible default None). Evidence-graph models `ClaimNode`/`EvidenceNode`/`EdgeClaimToEvidence`/`EvidenceGraph` (consumed by `converter/traceability_graph`). |
| `protocols.py` | Structural interfaces: `LLMClient.complete`, `PMSQuery` (`evidence_for_bucket`/`public_evidence`/`resolve`). |
| `validators.py` | `validate_kpi_sum`, `validate_citation_cover`, `validate_sections`, `normalize_kpi_weights`, `budget_constants`. |
| `ids.py` | `stable_id(*parts)` = sha256 of `|`-joined parts (deterministic ids). |

## RULES
- Models are FROZEN — never mutate; use `model_copy(update={...})`.
- `IR.validate_ir` enforces `trl_start < trl_end` and calls `validate_kpi_sum` (KPI weights must sum correctly). Construction fails loudly if violated.
- `keywords` capped at 5 (`Field(max_length=5)`).
- `validators.py` uses local `_KPILike`/`_PMSQueryLike` Protocols instead of importing models — deliberate, to avoid circular imports. Keep it import-light.
- Add a public symbol → update `__init__.py` re-exports AND its `__all__`.
