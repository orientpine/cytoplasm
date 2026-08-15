---
name: prompt
description: "버전형 프롬프트 자산을 canonical·overlay·legacy 계층에서 결정적으로 검색·조회·추가한다. 민감 본문은 agent 전용 private 저장소에만 두고 비-GLM 경로를 강제한다. W5-1."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [Prompt-Library, Versioning, Private-Storage, Deterministic]
prerequisites:
  commands: [python3, hermes]
---

# prompt

CLI: `python3 ~/.hermes/skills/prompt/scripts/prompt_cli.py`

## Commands

- `!prompt search <q>` → `prompt_cli.py search "<q>"`
- `!prompt get <id> [--version N]` → `prompt_cli.py get <id> [--version N]`
- `!prompt add` → create a mode-600 body file, then run:

```bash
python3 ~/.hermes/skills/prompt/scripts/prompt_cli.py add \
  --id <id> --category <task|research-background> --purpose "<one line>" \
  --model <glm-main|openai-codex|any> --tags "tag1,tag2" --body-file <600-file>
```

`add` only writes `~/.hermes/prompt-library/entries/<id>/v<N>.md`; an existing id
creates the next immutable version. Canonical and legacy files are read-only.

## Sensitive entries

When `get` reports `routing_tags=patent-sensitive`, never send its body to GLM or
post it publicly. Use `get --write-body <new-600-file>`, then make the single
outbound use through `openai-codex` / `gpt-5.4` with the routing tag at the start
of the request: `<routing-tags>patent-sensitive</routing-tags>`. The private body
is only under `~/prompts-private/` (700); the overlay keeps a metadata-only stub.

## Sandbox

`scripts/scenario.sh` is offline and verifies version increments, metadata-only
private split, legacy read-only indexing, and isolated imports.
