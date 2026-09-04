# LiteLLM routing policy

> **Scope — read this first.** This document has two layers.
>
> * **§1 Policy structure** is installation-independent. It is the part that carries the
>   safety properties (single alias, fail-closed budget, pre-call sensitive-tag rejection),
>   and a third-party operator should keep it.
> * **§2 Binding of this installation** holds the concrete values of cha's node — model ids,
>   budget amounts, key names, gateway paths, verification dates. **Those are examples for
>   anyone else: replace them with your own.** Nothing in the codebase parses this file, so a
>   replacement here has no runtime effect on its own; the values live in
>   `configs/litellm-staging/config.yaml` and `~/.hermes/config.yaml`.
>
> Third-party prerequisites and a minimal example config:
> [`docs/guide/third-party-runtime-prereqs.md`](../docs/guide/third-party-runtime-prereqs.md) §2.

## 1. Policy structure (installation-independent)

1. **One gateway alias.** The runtime addresses exactly one LiteLLM alias. Rebinding that alias
   to a different provider model must not require touching agent or skill configuration.
2. **Primary / fallback split.** The agent's primary conversational model may sit outside
   LiteLLM (subscription OAuth); the gateway alias then serves as the failover route
   (rate-limit / overload / connection) and as the model for batch pipelines.
3. **Deployment tagging.** Every deployment carries selection tags, and tag filtering stays
   enabled. Tags are a *deployment-selection* control, not the enforcement point.
4. **Per-key budget with a fail-closed cap.** Runtime keys are restricted to the gateway alias
   and carry a soft alert budget plus a hard cap over a fixed window.
   `fail_closed_budget_enforcement: true` is enabled — an accounting failure denies the call
   rather than allowing an unmetered one. No key material or webhook address is stored in this
   repository.
5. **Sensitive work never reaches the shared provider.** Callers mark sensitive requests with
   `metadata.tags=["patent-sensitive"]`. A deployed pre-call blocker rejects a tagged request to
   the gateway alias with HTTP 403 and the `no_deployments_with_tag_routing` marker **before a
   provider call**, so the agent must route that work to a non-gateway path instead.
6. **The fallback window is closed too.** The recall skill releases sensitive content into a
   conversation only when the agent's primary route is verified non-gateway, and prefixes each
   released row with a sentinel. The same pre-call guard rejects any gateway request whose
   message payload carries that sentinel, so a mid-conversation failover cannot leak
   recall-released content to the shared provider.
7. **The pre-call guard is the enforcement point.** The configured tag filter remains a
   deployment-selection control; the guard is the verified fail-closed enforcement point for a
   single-deployment gateway and preserves the intended no-deployment rejection semantics.

## 2. Binding of this installation (example — replace with your own)

| Item | Value on cha's node | Third-party note |
|---|---|---|
| Gateway alias | `glm-main` | any name; keep it single |
| Provider model behind the alias | `openai/gpt-5.6-luna` (`reasoning_effort: none`; 2026-09-03 까지 `zai/glm-5.2`, 같은 날 잠시 `gpt-5-mini`) | your provider/model id |
| Binding verified on | 2026-09-03 | record your own verification date |
| Agent primary model | `openai-codex/gpt-5.6-sol` (ChatGPT subscription OAuth, outside LiteLLM), `agent.reasoning_effort: high` | optional — a single OpenAI-compatible `/v1` endpoint is the minimum |
| Alias role | fallback for the primary model + batch pipelines (mail triage, meeting, twin_distill, report, …) | same structure |
| Deployment tags | `default`, `non-patent-sensitive` | same structure |
| Runtime virtual keys | `agent`, `peer` | your account names |
| Budget per key | `<monthly-soft-cap>` alert, `<monthly-hard-cap>` hard cap (`<budget-duration>`) | your amounts |
| Sensitive sentinel | `[[PATENT-SENSITIVE-RECALL]]` | keep the sentinel mechanism |
| Staged bundle | `configs/litellm-staging/` | same |
| Deployed gateway directory | `/home/ops/litellm-gateway/` | your path |
| Fallback-window guard added | 2026-07-22 | — |

Anthropic aliases remain deferred, and OpenAI uses Hermes OAuth outside LiteLLM.

## 3. Rebinding procedure

Steps 1–2 and 5 are installation-independent; the paths and amounts in steps 3–4 come from §2.

1. Confirm the replacement provider model through a live provider check and record the model ID and verification date here.
2. Change only `model_list[<alias>]` in `configs/litellm-staging/config.yaml`; preserve the non-sensitive deployment tags, tag guard, key budgets, and alias name.
3. Copy only the non-secret staged configuration to the deployed gateway directory (§2) and run `docker compose up -d --force-recreate litellm`. Do not remove the Postgres volume or regenerate virtual keys.
4. Verify authenticated `/health`, one redacted alias completion with a spend-row increase, the patent-tag HTTP rejection marker, and a temporary `$0.01` hard-cap rejection followed by restoration to the §2 soft/hard budget.
5. Save masked evidence under `docs/qa/`, add an infrastructure patch record, and push the repository commit before treating the rebinding as complete.
