# Codex OAuth routing policy

## Policy

1. **One provider and model.** Every interactive and non-interactive automation call uses
   provider `openai-codex` with model `gpt-5.6-sol` through the shared
   `automation.codex_llm` client.
2. **Fail closed.** A call succeeds only when the shared client receives exit code 0 and
   non-empty output. Missing OAuth credentials, quota, transport, timeout, or empty output
   raises an error to the caller. No caller may retry, downgrade, or route the request through
   another provider.
3. **Hermes invocation is pinned.** The client invokes Hermes with
   `--ignore-user-config`, so user configuration cannot introduce an alternate route. OAuth
   credentials remain available to Hermes while provider fallbacks are disabled.
4. **Sensitive work stays gated.** Deterministic sensitivity classification remains before every
   model call. Patent-sensitive or confidential content may be sent only through the Codex OAuth
   tier after the applicable gate approves it; a missing, malformed, or unapproved route is
   refused before a provider call.
5. **Subscription accounting.** The subscription tier is outside the LiteLLM budget; there is no
   second tier. Budget, authorization, or availability failures deny the request rather than
   selecting a different route.

## Installation binding

| Item | Value |
|---|---|
| Provider | `openai-codex` |
| Model | `gpt-5.6-sol` |
| Client | `automation.codex_llm.CodexClient` |
| Authentication | Hermes Codex OAuth credentials |
| Invocation guard | `--ignore-user-config` |
| Success condition | exit code 0 and non-empty stdout |
| Unavailable condition | missing credentials, quota, transport, timeout, or empty output |
| Alternate tier | none |

## Verification

Run a non-interactive completion as the agent user with an empty stdin and a minimal environment.
It must return plain, non-empty text on stdout. Repeat with a home that has no Codex credentials:
the invocation must fail with no stdout. Treat either result outside those conditions as a routing
failure; do not install a substitute provider or retry through a different route.
