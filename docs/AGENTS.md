# docs/ — 지식·증적 베이스 (탐색 가이드)

설계·작업분해의 단일 진실은 여기가 아니라 [.omo/plans/autophagy-agents.md](../.omo/plans/autophagy-agents.md).
이 트리는 규약(guide)·인프라 변경 이력(patch)·검증 증적(qa)으로 나뉜다.

## 하위 분류
| 폴더 | 내용 | 파일 규칙 |
|------|------|-----------|
| `guide/` | **설계 규약·운영 가이드 (규범)** — interop-규약, watcher-cron-설계규약, 스킬-제작, discord-server-architecture, decision-twin-스키마, doctype-usage, report-hub, onboarding-kit, 개인-위키, operations, reboot-recovery, incident-response, w0-* 셋업 등 | 주제별 kebab/한국어 |
| `patch/` | **인프라 변경 로그** — 게이트웨이/RAG/헬스체크/재배포 등 날짜 기록 | `YYYY-MM-DD-<slug>.md` |
| `qa/` | **웨이브 검증 증적 (마스킹)** — `.omo/plans`의 wave ID와 1:1. 원시 증적은 ops 전용 경로 | `W#-#/` `E#/` `F#/`, 실발송 단계는 `NN-real-*.md` |
| `troubleshooting/` | 재현 가능한 문제 해결 절차 | — |

## 상위 문서
- `features.md` — 기능 현황판(요약 뷰 + 신규 아이디어 + 후속 과제 요약표). 계획 문서의 파생.
- `follow-ups.md` — 후속 과제 전문(묶음별 `##` 헤딩) — **이 저장소가 지금 손댈 수 있는 열린 작업만**.
- `follow-ups-deferred.md` — 보류·인계(2026-08-26 분리): OWNER(소유자·노드) · BLOCKED(동결·벤더) · OBSERVE(관측 대기) · 해소 기록.
  **삭제가 아니라 이동**이고 원 `##` 헤딩을 양쪽에서 유지해야 한다 — 회계 가드 A9가 두 문서를 합쳐 읽어 이동과 삭제를 가른다.
- `done.md` — 완료 기능 전문.
  네 파일은 `tests/unit/test_features_board_conformance.py`가 함께 검사한다 — 한 파일만 읽으면 DONE이 빈 껍데기로 보여 검사가 공허해진다.
- `hardware-infra-openclaw.md` · `spark-활용-검토.md` — 하드웨어/노드 검토.

## 규칙
- **민감 본문·시크릿·URL은 여기에 절대 원시로 남기지 않는다** — qa 증적은 마스킹본만(원시는 `/srv/autophagy-private/...` ops 전용).
- 기능 추가/변경 시 **같은 커밋 사이클에서** guide/·SKILL.md·루트 AGENTS.md의 낡은 문구를 함께 갱신(루트 "문서 갱신 규칙").
