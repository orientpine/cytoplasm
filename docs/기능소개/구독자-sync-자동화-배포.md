# 구독자 sync 자동화 배포 (W-F3-B)

## 무엇을
관리형 스킬 채널의 구독자 틱(fetch → verify → quarantine)을 **정식 배포물**로 만들었다.
같은 틱 구현 하나를 두 경로가 공유한다 — 기존 Hermes no-agent cron(`automation/managed_sync/deploy.sh`)과
설치기의 **opt-in 컴포넌트**(`automation/managed_sync/systemd/` 타이머·서비스 쌍).

## 왜
`managed_sync_watch.py`는 이미 있었지만 **그것을 등록하는 것이 리포에 없었다** — 배포 스크립트도
systemd 유닛도 없어서, 관리자가 스킬을 발행해도 사람이 손으로 `python3 -m automation.managed_sync sync`를
기억해 실행할 때만 도착했다. 관리형 채널이 없애려던 침묵이 정확히 그 자리에 남아 있었다.

## 사용 시나리오

**설치 시(구독자)** — 컴포넌트는 **명시해야만** 설치된다. 이름을 대지 않으면 파일도 타이머도 생기지 않는다.

```bash
python3 -m automation.install --update-trust-key <key> --with-component managed-sync
```

두 번 실행해도 타이머는 하나다 — 유닛 해시와 활성 타이머를 대조하는 기존 설치 계획 로직이
2회차를 check-only로 만든다. 오타(`--with-component managed-sink`)는 조용히 무시되지 않고 거부된다.

**cha 노드(기존 Hermes cron)** — `automation/managed_sync/deploy.sh`.
provenance 가드 → `~/.hermes/scripts/managed_sync_watch.py`로 래퍼 전송 → 30분 cron 멱등 등록.

**happy path** — 관리자가 발행하면 30분 안에 한 틱이 릴리스를 검증해 **격리(quarantine)** 에 두고
소유자에게 알림 1건을 보낸다. 알림에는 스킬 이름·시퀀스·digest 앞 12자만 담기고,
**활성화는 자동이 아니다**(D3): `readlink live/managed-<name>`은 그대로이며,
`activate-instructions`가 알려주는 owner-gated 명령에 본인이 ✅를 눌러야 마운트된다.

**거부 경로** — 서명이 맞지 않는 릴리스는 `SYNC-FAILED ... reason=BAD-SIGNATURE`로 **사유가 노출된 채**
거부되고 격리에도 들어가지 않는다. 이 실패는 매 틱 반복되므로 DM은 보내지 않는다 —
반복 실패를 30분마다 DM하면 그 자체가 홍수다. 사유는 저널(=유닛 로그)에 매 틱 남는다.

**겹친 틱** — 앞선 틱이 아직 돌고 있으면 다음 틱은 리포 표준 `FileKeyLease`를 잡지 못하고
**exit 0으로 조용히** 끝난다. 일어나지 않은 사건을 사건으로 보고하지 않기 위해서다.

## 관련
- 틱: `automation/managed_sync/cron/managed_sync_watch.py` (lock=`FileKeyLease`, 알림=`automation/owner_notice.py`)
- 배포: `automation/managed_sync/deploy.sh` · `automation/managed_sync/systemd/`
- opt-in 레지스트리: `automation/install/components.py` (다음 컴포넌트는 여기 두 줄)
- 승인: 이 웨이브는 **승인 표면을 만들지 않는다.** 알림은 게이트가 아니라 통지이며,
  마운트 승인은 기존 `deploy-skill.sh --activate-managed` 4단계 게이트가 그대로 소유한다.
- 증적: `docs/qa/W-F3-B/summary.txt` (증명된 것과 실호스트에서만 가능한 것을 나누어 기록)
