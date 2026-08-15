---
name: repair
description: "오류·스킬 실패·헬스체크 실패를 레닥션된 개인 Kanban 수리 티켓으로 기록한다. 전체 로그는 ops 전용 경로에만 보관한다. W6-1."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [Repair, Kanban, Private-Logs, Redaction]
prerequisites:
  commands: [python3, hermes, ssh]
---

# repair — 오류 수리 티켓

`!repair`, “수리해줘”, “이상해” 요청은 모두 같은 수리 티켓 경로를 사용한다.
카드는 unassigned + `blocked/needs_input`으로 만들어져 dispatcher가 LLM worker를
시작하지 않는다.

## 절대 규칙

1. 입력 오류 전문은 Kanban, Discord, repo 또는 docs/qa에 출력하지 않는다.
2. 공개 표면에는 레닥션된 발췌, SHA-256, ops private-log 경로만 남긴다.
3. 동일한 오류 signature는 새 카드를 만들지 않고 기존 카드의 occurrence comment를 늘린다.
4. 개인 수리는 자동 실행하지 않는다. 카드 상태는 사람이 검토할 때까지 blocked로 유지한다.

## Command

```bash
# !repair [증상]
python3 -I ~/.hermes/repair/automation/repair/repair_cli.py manual "!repair [증상]"
```

명령 출력은 ticket id와 occurrence count만 포함한다.
