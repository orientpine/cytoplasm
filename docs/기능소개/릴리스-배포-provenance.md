# 봉인된 릴리스의 배포 provenance

## 무엇을
배포 가드가 던지는 질문("배포되는 모든 바이트가 `origin/main`에 있는가")을, git 워킹트리가 아니라
**읽기 전용 릴리스 스냅샷**에 대해서도 답할 수 있게 한다. 릴리스 트리 **전체**를 그 커밋의 트리와
대조해 하나라도 어긋나면 배포를 막는다.

## 왜
DG-5가 노드 런타임을 `.git` 없는 봉인 스냅샷(`/srv/autophagy-agent-current`)으로 옮기면서,
워킹트리 blob 대조로 동작하던 provenance 가드가 거기서는 아예 동작할 수 없게 됐다. 그 결과
**⑦ 공급망 워처의 자율 배포 경로는 단 한 번도 완주한 적이 없었다** — 소유자가 ✅를 눌러도 매 tick
이렇게 죽었다(2026-08-02 실측):

```
[deploy-provenance] DEPLOY-BLOCK: /srv/autophagy-agent-current is not a git checkout
```

사람이 수동으로 돌릴 때는 개발 체크아웃에서 실행돼 통과했으므로 드러나지 않았다. 자율 경로를
끝까지 돌려본 적이 없었기 때문이다.

우회(`DEPLOY_ALLOW_UNPUSHED=1`)는 답이 아니다. 그것은 샌드박스 탈출구이며, 최고 권한 자율
경로에 상시로 걸면 가드가 정책이 아니라 장식이 된다.

## 무엇을 믿지 않는가 — 설계의 중심

- **`.origin-sha`를 믿지 않는다.** 설치기(`release_store.py`)는 호출자가 준 아카이브와 호출자가 준
  SHA를 그대로 마커에 적는다. 즉 마커는 *검증되지 않은 신원 주장*이다. 어느 커밋과 대조할지
  고르는 데만 쓰고, 그 주장 자체를 트리로 검증한다.
- **tracked 목록을 믿지 않는다.** 커밋에 없는 여분 파일은 `ls-files`에 안 잡히면서 배포 아카이브에는
  실려 간다(2026-08-01에 실제로 그렇게 새 파일이 prod에 닿았다). 봉인된 트리에는 인덱스가 없어
  "untracked"라는 개념 자체가 없으므로, **경로 집합이 정확히 같은지**로 대체한다. 유일한 예외는
  설치기가 만든 `.origin-sha` 하나다.
- **조상 커밋도 받지 않는다.** 뒤처진 릴리스는 재조정 타이머가 곧 최신으로 바꾼다 — 미루는 비용은
  한 tick이고, 받아들이는 비용은 소유자가 승인한 것보다 낡은 코드다.

## 사용 시나리오

### 정상 경로
1. 소유자가 `#approvals`에서 ✅.
2. ⑦ 워처가 승인을 집어 릴리스의 `deploy-skill.sh`를 재개한다.
3. 가드가 릴리스 모드를 인지 → `release_provenance.py`가 트리 전체를 커밋과 대조 →
   `OK: N file(s) match origin/main at <sha>`.
4. 파이프라인이 정상 진행해 MOUNT.

### 차단 경로 (전부 hard stop)
| 상황 | 결과 |
|---|---|
| 파일 한 바이트라도 커밋과 다름 | `release file differs from the commit: <경로>` |
| 커밋에 없는 여분 파일 | `release has files absent from the commit: <경로>` |
| 커밋에 있는 파일 누락 | `release is missing committed files: <경로>` |
| 심링크·특수파일 | `release contains a non-regular entry: <경로>` |
| 마커가 디렉터리명과 불일치 | `.origin-sha ... does not match the release directory name` |
| 릴리스가 tip이 아님 | `release <sha> is not the origin/main tip <sha>` |
| 객체 저장소 부재·커밋 미지 | 차단 (확인 불가는 통과가 아니다) |
| 파일에 쓰기 비트 | `release file is writable: <경로>` |
| exec 비트가 커밋 모드와 다름 | `executable bit disagrees with the commit: <경로>` |

## 주의
- **경로 열거는 NUL이어야 한다.** `ls-tree` 기본 인용은 한글 경로를 C-escape로 바꾼다 — 그대로 쓰면
  이 리포의 한글 문서 45개가 전부 불일치로 잡힌다(실측).
- **blob oid는 `ls-tree`가 주는 것을 쓴다.** 파일당 `git rev-parse`를 부르면 1500개 서브프로세스가 되어
  2분 주기 워처가 감당할 비용이 아니다.
- **`current`는 물리 경로로 고정한다.** 재조정 타이머가 배포 도중 심링크를 뒤집을 수 있고, 한 릴리스를
  검증하고 다른 릴리스를 포장하면 검사 자체가 무의미해진다.
- **검증 계정에는 fetch 자격증명이 없다**(최소권한 — `agent`는 미러를 읽기만 한다). 그래서 "방금 fetch한
  tip" 대신 ops 재조정 타이머가 2분마다 갱신하는 미러의 `origin/main` ref를 기준으로 쓴다. 그 계정에
  write 자격증명을 주는 것이 더 큰 후퇴이기 때문이다.
- **검증기는 자신이 검증하는 릴리스 안에 있다.** root/ops 침해가 위협 모델에 들어오면 이 신뢰 기점은
  충분하지 않다 — 그때는 설치 시점 검증(루트 소유 헬퍼 또는 서명된 릴리스 attestation)이 필요하다.

## 관련
- 검증기: `automation/release_provenance.py` (`verify_release`)
- 배선: `automation/deploy_provenance.sh` (`deploy_provenance_release_check`)
- 릴리스 설치: `automation/release_store.py`, `automation/converge-release-runtime.sh`
- 소비자: `automation/deploy-skill.sh`, ⑦ 워처([소개](공급망-승인-재조정.md))
- 테스트: `tests/unit/test_release_provenance.py` (14건)
- 규칙: 루트 `AGENTS.md`「배포 provenance 규칙」·「ops 체크아웃 단방향 규칙」
- 증적: `docs/qa/B-5/live-activation.txt`
