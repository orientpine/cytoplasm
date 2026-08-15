# Peer attestation 자동 재검증

**완료:** 2026-07-29 · **티켓:** repair `t_24a0ea01` · **범위:** 스킬 배포

## 무엇을
유효한 owner ✅는 그대로 보존하고, 배포 실행 시 peer attestation만 30분 TTL을 넘겼다면 같은 content hash·nonce·action·destination을 peer가 다시 검증해 새 증명을 남긴 뒤 배포를 계속한다.

## 왜
기존에는 peer TTL이 owner 결정의 수명처럼 동작해, 늦게 실행한 배포가 이미 받은 ✅를 폐기하고 같은 요청을 다시 게시했다. 비동기 승인에서는 cha가 동일 스킬을 반복 승인해야 했다.

## 사용 시나리오
1. **정상:** cha가 승인한 뒤 45분 후 같은 스킬을 배포한다 → owner 메시지·nonce는 그대로 유지 → peer가 샌드박스 바이트를 다시 검증하고 새 PASS reply를 게시 → owner 재승인 없이 MOUNT.
2. **거부:** 요청 본문/hash/action/destination이 달라졌거나, ⛔ 취소·✅ 철회·supersede·nonce 재사용이 발견된다 → refresh를 시작하지 않고 fail closed, MOUNT 0건.
3. **동시 실행:** 같은 스킬 배포 2개가 겹친다 → 기존 `FileKeyLease` 기반 실행 lock을 먼저 얻은 1개만 refresh부터 승인 consume까지 진행하고 다른 실행은 exit 8로 중단한다.

## 관련
- 파이프라인: `automation/deploy-skill.sh`
- refresh 자격 판정: `automation/skill_gate_refresh.py`
- peer 생성/검증: `automation/peer_attest.py`, `automation/peer_attestation.py`
- 동시성: `automation/deploy_execution_lock.py`, `automation/interop/approval_lease.py`
- 증적: `docs/qa/RTS-3/d3-attestation-refresh.txt`
