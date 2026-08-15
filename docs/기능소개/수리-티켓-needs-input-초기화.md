# 수리 티켓 `needs_input` 초기화

## 무엇을

새 수리 티켓을 Hermes kanban의 기본 `ready` 상태로 만든 직후
`blocked/needs_input`으로 전환한다. 생성 시 `triage`를 강제하지 않으므로 실제 보드 상태와
“사람의 수리 검토 전에는 작업자를 배정하지 않는다”는 설계 의도가 일치한다.

## 왜

Hermes CLI는 `triage` 카드에 대한 `block` 요청을 실행하지 않는다. 기존 어댑터는 카드를
`--triage`로 만든 뒤 블록했기 때문에 명령이 성공 코드로 끝나더라도 카드가 계속
`triage`에 남았다. 기본 `ready` 생성은 같은 `block --kind needs_input` 전이를 허용한다.

## 사용 시나리오

- **정상 경로**: 수리 감지가 새 티켓을 만들면 `ready`로 생성되고 즉시
  `blocked/needs_input`이 된다. 소유자 검토 전 LLM 작업자는 배정되지 않는다.
- **실패 경로**: kanban 생성 또는 블록 명령이 비정상 종료하면 기존 fail-closed 오류 처리가
  적용된다. 생성 단계에서 `triage`를 선택해 블록이 조용히 무시되는 경로는 제거됐다.

## 관련

- 어댑터: `automation/repair/repair_cli.py`의 `HermesKanban`
- 흐름: `automation/repair/repair_core.py`의 `RepairService._create_ticket`
- 회귀 테스트: `tests/unit/test_repair_tickets.py`
- 라이브 증적: `docs/qa/W6-1/02-needs-input-create-fix.md`
