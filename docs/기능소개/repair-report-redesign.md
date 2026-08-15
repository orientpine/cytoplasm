# 수리 보고 경로 재설계

## 무엇을

수리 티켓이 **완료(done)** 또는 **재개(reopened)** 로 종결될 때 `#agents-log`에 보고가 정확히 한 번
나가도록 경로를 다시 짰다. ops측 보드는 보고를 직접 보내지 않고 capability에 묶인 enum-only 요청을
큐에 넣기만 하며, agent 소유 소비자 cron이 5분마다 그것을 소비해 전송하고 불변 영수증을 남긴다.

## 왜

기존 경로는 종결 상태 저장이 보고보다 먼저였다. 그 사이에 프로세스가 죽으면 **보드는 종결인데 보고는
영영 나가지 않았고**, 유실됐다는 사실조차 남지 않았다. 반대로 재시도를 넣으면 같은 종결이 두 번,
세 번 보고되는 쪽으로 깨졌다. 게다가 ops가 보고 내용을 자유 텍스트로 만들면 수리 로그 원문(커밋 해시
포함)이 공개 채널로 샐 수 있었다.

재설계는 세 가지를 동시에 잡는다.

- **유실 0** — 크래시 창은 ops 보정기가 닫는다. 보고 마커가 한 번도 쓰이지 않은 종결을 찾아 큐에 다시
  넣는다.
- **중복 0** — 의미 영수증과 보고 마커가 같은 조합의 두 번째 전송을 구조적으로 막는다.
- **누설 0** — 큐에 흐르는 것은 enum(`operation`·`reason_code`)과 식별자뿐이다. 자유 텍스트가 없다.

## 사용 시나리오

### 정상 경로

1. cha가 수리 승인 ✅를 누르고 패치가 적용되면 ops 보드가 티켓을 `done`으로 종결한다.
2. 같은 순간 보드가 `complete`/`applied` 요청 한 줄을 큐에 넣는다. 그 줄에는 detect 시점에 agent가
   발급한 HMAC capability가 실린다.
3. 5분 안에 `repair-report-consumer` cron이 tick하며 그 줄을 소비한다. 카드 상태를 확인하고,
   `#agents-log`에 보고를 보내고, ACK와 마커를 쓴다.
4. cha는 `#agents-log`에서 종결 보고를 본다. 같은 종결로 두 번째 메시지는 오지 않는다.

라이브 검증(2026-08-14)에서 카나리 요청 1줄이 **수동 실행 0회**로 스케줄러에만 소비되어, 터미널 ACK와
Discord 메시지 정확히 1건을 남겼다.

### 거부 경로

위조된 요청은 발화하지 못한다. ops가 만든 줄의 `mac`을 1비트만 바꾸면 소비자는 그 줄을
`dead(bad_capability)`로 종결하고 **전송을 0회 수행한다** — 예약도 의미 영수증도 만들지 않으므로,
뒤이어 도착한 정상 요청은 정상적으로 전이·전송된다. 등록부에 없는 티켓도 같은 방식으로
`dead(unknown_ticket)`이 된다.

### 수용된 한계 (소유자 확정)

- capability는 **티켓·occurrence만** 인증하고 `operation`·`reason_code`는 인증하지 않는다
  (2026-07-25). 상한은 `(ticket, occurrence)`당 최대 7건(1 complete + 6 reopen)이고 전부 영수증에
  남는다.
- 보정기는 같은 `(ticket, operation, reason_code)`의 **두 번째 유실은 복구하지 않는다**
  (2026-07-26). 그 경우에도 첫 보고가 이미 나갔으므로 보드와 보고는 일치 상태다.

## 관련

- 운영 절차·활성 cron·잔여 위험: [operations.md](../guide/operations.md) §5
- detect 런타임 갱신: [repair detect 런타임 배포](../guide/repair-detect-runtime-배포.md)
- 경로·권한 원장: [gate-ledger-inventory.md](../guide/gate-ledger-inventory.md),
  [w0-4-account-setup.md](../guide/w0-4-account-setup.md)
- 증적: `docs/qa/RRO-0/`, `docs/qa/RRO-1/`, `docs/qa/RRO-2/`
- 승인 게이트: 이 경로 자체에는 소유자 승인이 없다. 승인은 **패치 적용** 단계에 있고
  ([수리 승인 내용 바인딩](수리-승인-내용-바인딩.md)), 보고는 그 결과의 사후 통지다.
