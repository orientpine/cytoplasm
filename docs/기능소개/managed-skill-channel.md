# 기능 소개 — 관리형 스킬 채널 (Managed Skill Channel)

**완료:** 2026-07-25 · **웨이브:** MS Wave · **스킬:** `managed-hello-autophagy` (v1~v5)

## 무엇을
cha가 관리형 스킬을 SSH 서명된 git 태그로 비공개 피드(`managed-skills` 리포)에 발행하고, 각 연구원 노드가 이를 자동으로 fetch→검증→격리(quarantine)한 뒤, **연구원 본인의 ✅ 승인으로만** 활성화되는 배포 채널이다.

## 왜
이전엔 연구원 간 결합이 온보딩 킷(수동 복사)뿐이라, 스킬을 안전하게 갱신·전파할 방법이 없었다. 출처 증명(provenance)·회수(revocation) 수단도 없었다.

## 핵심 동작 (불변식)
- **자동 단계는 격리소에서 멈춘다**: 활성화는 매번 연구원 ✅ + 로컬 peer 증명을 거친 기존 4단계 게이트 통과가 필수다(SI-1).
- **8단계 fail-closed 검증**: 서명·주체·태그↔내용 바인딩·스키마·태그일치·시퀀스 단조성·이전 다이제스트 체인·소스 다이제스트 재계산·회수 목록 중 하나라도 실패하면 격리소에조차 들어가지 않는다(SI-2).
- **`managed-` 접두사 네임스페이스 격리**: 일반 배포는 `managed-*`를 만들 수 없고, 이름 충돌 시 우선순위 없이 양방향 fail-closed로 막고 연구원이 직접 하나를 고른다(SI-4).
- **회수는 자동 삭제가 아니다**: 새 릴리스가 이전 버전을 회수하면 신규 활성화만 즉시 차단하고 삭제 요청만 안내한다. 실제 제거는 소유자가 직접 `--remove`를 실행해야 한다(SI-7).

## 사용 시나리오
1. **(발행)** cha가 카나리 스킬을 발행 → 배포 게이트 ✅ + 발행 ✅ 두 번 → SSH 서명 태그 `<skill>/v<N>` 생성·푸시 + `#skill-releases` 공지. 이 시점엔 어느 노드에도 활성화되지 않는다.
2. **(구독·활성화)** 연구원 노드가 `managed_sync sync` → 검증 통과분만 격리소 적재 → 활성화 요청 → `#approvals`에 발행자·태그·시퀀스·매니페스트 다이제스트(provenance)가 표시된 요청 게시 → 연구원 ✅ → 마운트 + invoke smoke.
3. **(회수)** 새 릴리스가 이전 다이제스트를 회수 → 다음 sync에서 해당 다이제스트의 신규 활성화가 차단되고 삭제 요청이 표시됨 → 연구원이 직접 `deploy-skill.sh <skill> --remove` 실행 시 라이브 심링크가 제거된다.

## 관련
- 상세 규약: `docs/guide/managed-skill-channel.md`
- 발행: `automation/managed_skills/` (manifest, publish_cli, announce)
- 구독: `automation/managed_sync/` (state, fetch, verify, quarantine, pipeline, revoke, cli, cron)
- 루트 헬퍼: `automation/skill_store.py`의 `install-managed`
- 파이프라인: `automation/deploy-skill.sh` (`--approve-only`, `--activate-managed`, `--remove`)
- 승인 게이트: 개인 서버 `#approvals` 재사용
- 라이브 증적: `docs/qa/MS-O/`, 최종 검증 `docs/qa/MS-F/ms-f2-final-verification.txt`
