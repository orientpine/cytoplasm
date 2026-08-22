# hermes_compat 패치가 빠진 채 게이트웨이가 돌고 있는지 확인하기

## 증상

게이트웨이는 `active/running`이고 Discord 응답도 정상인데, busy 경로 메시지가
skill-generation 관측(W6-4)이나 meeting-gate fail-closed veto(W2-3)를 타지 않는다. 또는
소유자 DM에 👀/✅ 영수증이 붙지 않는다. 유닛 상태·Discord 연결성으로는 **원리적으로 안 잡힌다**
— 패치가 빠져도 벤더 코드는 잘 돌기 때문이다.

## 확인

```bash
# agent 계정에서 (읽기 전용, 아무것도 바꾸지 않는다)
python3 -m automation.hermes_compat.patch_state --install-root ~/.hermes/hermes-agent
```

| exit | 의미 | 다음 행동 |
|---:|---|---|
| 0 | 매니페스트의 전 패치가 적용돼 있다 | 없음 |
| 1 | 하나 이상이 빠졌다 | 아래 「복구」 |
| 2 | 판정 불가(매니페스트·대상 파일을 읽을 수 없음) | 경로·권한부터 확인. **빠진 것으로 읽지 말 것** |

출력은 패치 id별로 `PATCHED / MISSING / UNKNOWN`과 근거를 한 줄씩 낸다.

## 원인 (실측 2026-08-16~18)

`hermes update`가 벤더 소스를 교체하면서 만든 autostash가 복원되지 않아 패치 3종이 통째로
빠졌고, 프로덕션이 이틀을 그 상태로 돌았다. 그때 매니페스트의 notes는
"healthcheck should surface a missing patch"라고 적고 있었지만 `automation/healthcheck.sh`
에는 그런 검사가 한 줄도 없었다 — 장애 중에만 읽히는 잘못된 안심이었다.

## 복구 (소유자 작업 — 외부효과)

**한 스크립트만 돌리면 절반만 복구된다.** 캐리어를 올리는 스크립트가 둘이고 목적지도 다르다:

- `automation/hermes_compat/deploy.sh` → `patch_busy_dispatch.py`를 `hermes_compat/`에
- `automation/hermes_compat/deploy-owner-dm.sh` → `patch_busy_fifo.py`·`patch_discord_receipts.py`를 `appliers/`에

둘 다 실행한 뒤 위 프로브를 다시 돌려 exit 0을 확인한다. 게이트웨이 재시동은
「게이트웨이 재시동 규칙」대로 **agent·peer를 함께** 한다. 이 절차 자체는 외부효과라 에이전트가
대신 실행하지 않는다 — 소유자 원장 항목이다.

## 알려진 한계

- 이 프로브는 아직 `automation/healthcheck.sh`에 배선되어 있지 않다. 지금은 사람이 돌리거나
  복구 절차의 마지막 단계로 돌린다.
- 마커 존재만 본다. 마커가 있는데 seam이 상류 리베이스로 옮겨간 경우는 각 applier의
  `verify` 서브커맨드(exact-preimage + compile gate)가 판정한다.
