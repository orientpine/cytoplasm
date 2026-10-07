# MODULE: pipeline

Orchestration + CLI + repro harness. The only place that wires every module together. (13-node DAG overview lives in `../AGENTS.md`.)

## FILES
| File | Role |
|------|------|
| `orchestrator.py` | `draft(...)` runs nodes 1–9, `render(...)` runs nodes 10–13, and backward-compatible `run(...)` composes both while checking the fixed `NODE_NAMES` order. `RenderContext` carries profile/image/figure options to the HWPX boundary for their dedicated consumers. Owns HWPX assembly order (`_render`, `_gantt_schedule`) and all sidecar JSON writes. |
| `cli.py` | `main(argv)` → exit code. Proposal entry points are `draft`, `render`, and backward-compatible `run`; collaboration/research/validation subcommands remain separate. Emits JSON-line lifecycle events and converts expected input/runtime errors to exit 1. `_build_llm` requires an API key for live/record drafting. Entry point `kimm-docbot` (pyproject). |
| `harness.py` (62) | `run_demo(out_path)` + `assert_deterministic(n=3)` (runs 3×, sha256-compares). Offline demo via `python -m kimm_docbot.pipeline.harness`. |

## HARD RULES
- **`NODE_NAMES` and the composed `draft()` → `render()` call order in `run()` MUST stay in sync** — every stage appends a `NodeLog(inputs_hash, outputs_hash)` via `_append_log` (`stable_id` of `repr(...)[:200]`). Adding a stage = edit both.
- `run()` raises `ValueError("Empty corpus")` on no docs and re-raises gate failures (sanitize_gate, validate_public_artifact) as `ValueError` — `cli._run` converts these to exit 1; never swallow them.
- `_render` assembles in fixed order: section text first (sorted by `section_id`), then KPI table, then gantt, then `replace_entry`→`sync_prv_text`→`repack`. Reordering breaks HWPX/preview parity.
- All JSON writes go through `_write_json` (`sort_keys=True`, `ensure_ascii=False`, trailing `\n`) — determinism. Do not `json.dumps` directly into artifacts.
- `_ReplayGracefulLLM` wraps the replay LLM: a `CacheMissError` for **advisory roles** (`_ADVISORY_ROLES = {critic, judge}`) returns `""` instead of raising, and — by NOT being a `CachingLLMClient` — it makes `orchestrator.run` SKIP the LLM critic, so default replay reproduces MockLLM output byte-for-byte. Essential roles still raise on a miss.

## GOTCHAS
- `_revise_or_keep` swallows `AssertionError` from `revise` and keeps the original drafts — revision is best-effort, never fatal.
- `_all_units` reaches into `pms._by_bucket` (private) to emit a sorted, deterministic unit list for the PMS snapshot — known coupling with `converter/pms.py`; keep that attr's shape stable.
- `PROJECT_ROOT = parents[3]`; `SEED_HWPX` and `DEFAULT_LLM_FIXTURE` are path-derived — moving this file changes the depth.
- `research-convert`/`corpus-lint` lazy-import their `converter` `main()` and delegate argv — cli.py stays import-light.
- `judge` subcommand (`_judge`) lazy-imports `agents.judge.judge_proposal` + `hwpx.validate.text_extract`; on a replay cache-miss it emits neutral placeholder scores (`judge_cache_unpopulated`) rather than failing.
