# Hermes model routing policy

## Policy

1. **One place picks the model (owner decision 2026-09-29).** Every interactive and
   non-interactive call starts on the main model named in the `model:` block
   (`provider`/`default`) of the account's `~/.hermes/config.yaml`. The Discord gateway reads it
   there; batch automation goes through the shared `automation.codex_llm` client, whose argv
   carries neither `--provider` nor `-m`, so Hermes applies the same block. No code, skill or
   environment variable names a model — `tests/unit/test_model_single_source_conformance.py`
   fails when one does. Changing the main model or the fallback is an edit to that file alone.
2. **One fallback chain, owned by Hermes.** When the primary cannot answer (rate limit or quota,
   server error, auth failure, invalid response), Hermes switches to the `fallback_providers`
   chain in the same `~/.hermes/config.yaml`. The gateway and the batch client read that one
   chain — the batch client does **not** pass `--ignore-user-config`, because that flag drops the
   user config and the chain with it. Fallback is scoped to one turn (gateway) or one call
   (batch); the primary is tried again next time unless Hermes knows its reset time has not
   passed yet.
3. **Fail closed when the chain is exhausted.** A call succeeds only when the shared client
   receives exit code 0 and non-empty output. When the primary and every fallback fail, the error
   reaches the caller. No caller retries on its own or opens a route outside the shared client;
   the only place to add or remove a route is `fallback_providers`.
4. **Sensitive work stays gated.** Deterministic sensitivity classification remains before every
   model call. Patent-sensitive or confidential content may be sent only through the shared Hermes
   route (Codex primary plus the configured fallback chain) after the applicable gate approves it.
   Since 2026-09-22 the owner permits the xAI fallback for patent-sensitive text as well. A
   completer outside the shared client is refused before a provider call.
5. **Accounting.** Codex and SuperGrok are subscriptions outside the LiteLLM budget. A call that
   falls back is served, and counted, by the xAI account.
6. **Logs name who answered.** Every masked routing log keeps `provider` and
   `model: hermes-config` (the config decided) and records the route that actually answered in `served_provider`/
   `served_model`, read from Hermes' `--usage-file` report (`automation.codex_llm.complete_served`).
   A missing report is logged as `unknown`, never assumed to be Codex.

## Installation binding

| Item | Value |
|---|---|
| Main model | `model.provider` / `model.default` in `~/.hermes/config.yaml` (2026-09-29: `openai-codex` / `gpt-6-sol`) |
| Fallback chain | `fallback_providers` in the same file (2026-09-29: `xai-oauth` / `grok-4.7`) |
| Batch client | `automation.codex_llm.CodexClient` — `hermes -z <prompt> -t todo` (no model, no provider) |
| First install | `automation/provision-agent.sh` writes the initial block; afterwards only the owner edits it |
| Peer account | follows the agent block — `python3 -m automation.model_sync [--apply]` (operator) copies `model:`/`fallback_providers:` to the peer; operator-mode doctor reports `model-parity` |
| Proposal refine | shared Hermes route (no Codex CLI); rules from im-not-ai `humanize-korean/references/quick-rules.md` |
| Authentication | `hermes auth` — Codex OAuth and xAI Grok OAuth (SuperGrok), both stored for the agent account |
| Success condition | exit code 0 and non-empty stdout |
| Unavailable condition | every route in the chain failed (credentials, quota, transport, timeout, or empty output) |

## Verification

Where the owner sees the pair without running anything: every release-applied notice in
#notifications carries a `모델:` line with both accounts' main model, fallback and `agent.max_turns`
(`automation/release_models.py`, read by the workstation sweep over ssh). On demand:
`python3 -m automation.doctor` as the agent account prints
`주 모델 <provider>/<model>` and `폴백 <provider>/<model>`; the file itself is
`/home/agent/.hermes/config.yaml`; what actually answered each call is `served_provider`/
`served_model` in the skill routing logs (`~/.hermes/*/logs/llm-calls.jsonl`).

Run a non-interactive completion as the agent user with an empty stdin and a minimal environment.
It must return plain, non-empty text on stdout. `hermes fallback list` as the agent user must show
the chain above; a missing xAI login makes the fallback fail, which leaves the primary's own error
as the result (the same outcome as having no fallback). Do not add a provider anywhere other than
`fallback_providers`.
