---
name: topics
description: "`!topics add|list|remove`로 arXiv 주간 연구동향 키워드를 관리한다. 민감도 규칙 적중어는 등록·자동제안 모두 거부하며 외부 arXiv 조회로 전송하지 않는다."
version: 1.0.0
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Research, arXiv, Weekly, Safety, Autophagy]
prerequisites:
  commands: [python3]
---

# 주간 연구 동향 키워드 (topics)

등록 키워드는 agent 개인 레지스트리 `~/.hermes/state/research-topics.yaml`에만
저장된다. 매주 Hermes cron이 **등록된 안전 키워드만** arXiv API로 전송한다.

## 명시 명령

```bash
python3 ~/.hermes/skills/topics/scripts/topics_cli.py add "<일반 연구 분야>"
python3 ~/.hermes/skills/topics/scripts/topics_cli.py list
python3 ~/.hermes/skills/topics/scripts/topics_cli.py remove "<등록 주제>"
```

- `!topics add <주제>`: `TOPIC-ADDED`이면 저장 완료, `TOPIC-EXISTS`이면 기존
  항목을 유지한다.
- `!topics list`: 현재 등록 목록을 표시한다.
- `!topics remove <주제>`: 정확한 정규화 항목을 제거한다.

## 민감도 게이트

add와 대화 기반 제안은 실행 전에 `configs/sensitivity-rules.yaml`의 결정적
키워드·정규식 게이트를 통과해야 한다. `TOPIC-REFUSED` 또는
`TOPIC-SUGGEST-REFUSED`가 나오면 후보를 저장하거나 arXiv·LLM에 보내지 말고,
출력의 일반화 안내만 cha에게 전달한다.

## 대화 기반 제안

대화 중 주간 추적에 적합한 **일반 연구 분야**가 보이면 저장 전에 다음으로
제안 가능 여부만 확인한다. 이 명령은 레지스트리를 바꾸지 않는다.

```bash
python3 ~/.hermes/skills/topics/scripts/topics_cli.py suggest "<후보>"
```

`TOPIC-SUGGEST`일 때만 cha에게 “등록할까요?”라고 묻고, cha가 동의하면 `add`를
실행한다. 제안 후보의 원문을 외부 도구·arXiv·LLM에 전달하지 않는다.

## 주간 리포트

`research-trends` Hermes cron이 등록 항목별 arXiv 링크와 한국어 동향 정리를
하나의 DM으로 보내고, 같은 리포트를 W2-4 개인 RAG 경로로 적재한다. arXiv
장애 항목은 실패 안내가 포함된 부분 리포트로 끝나며 자동 재시도 루프가 없다.
