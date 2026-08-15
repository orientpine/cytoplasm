# 마운트 스킬 ABI 가드 (DG-1 C)

## 무엇을
라이브 마운트된 스킬 스냅샷이 **현재 ops 체크아웃의 공유 라이브러리를 여전히 호출할 수 있는지**를
AST + `inspect.signature().bind`로 정적 검사한다(`automation/skill_library_abi.py`).

## 왜
라이브 스킬은 런타임에 `automation.interop.*`를 ops 체크아웃에서 import하는 **동결된 스냅샷**이다.
`deploy-skill.sh`의 첫 단계가 ops `git pull`이라, **무관한 스킬 배포 한 번이 이미 라이브인 여러 스킬 밑의 라이브러리를 동시에 옮긴다.**
AS-3.2가 `DiscordChannelDirectory`에서 `approval_env_var`를 제거했을 때, 그 인자를 넘기던 세 라이브 스킬(mail·budget·patent-prep)이
동시에 `TypeError`로 깨졌다 — 호출부가 함수 **안**에 있어 import만으로는 안 터지고, 사람의 수동 확인으로만 잡혔다.

## 사용 시나리오

### 정상 경로
1. 배포/착지 시 ff-pull로 ops 라이브러리가 움직인다.
2. `deploy-skill.sh`(마운트 직전)·`land.sh`(동기화 직후)가 `/srv/autophagy-skills/live/*` 전 스냅샷을 스캔한다.
3. 각 스냅샷의 `scripts/*.py`를 AST로 걸어 `_repo_module("x").Symbol(...)` 호출을 찾고, 현재 라이브러리 시그니처에 kwarg 이름을 bind한다.
4. 전부 통과하면 `ABI-OK`, 배포·착지 계속.

### 실패·거부 경로
- 스냅샷이 없는 kwarg를 넘기면 → `ABI-VIOLATION … unexpected keyword argument '…'` → **WARN**(`MOUNT-ABI-WARN`/`LAND-ABI-WARN`), 배포는 계속.
  블록하면 이미 소비된 소유자 승인을 고아로 만들어 더 나쁘다. 실제 파손은 승인 흐름의 fail-closed(게시 거부)로 나타난다.
- 검사기 자체가 크래시해도 → WARN(가드가 깨져도 배포를 깨지 않는다).
- `DEPLOY_ABI_STRICT=1`/`LAND_ABI_STRICT=1`이면 → 파손을 블록으로 승격(오탐율 관찰 후 기본값 검토).

### 판단하지 않고 건너뛰는 것(오탐이 도구를 죽인다)
- `**kwargs`/`*args`(키워드 이름·인자 수 미상), import 불가한 라이브러리, 라이브러리에서 사라진 심볼, `_repo_module` 규약 밖 호출.
  이들은 `Skip`으로 기록될 뿐 절대 위반으로 보고하지 않는다. 오탐 바닥은 테스트로 고정: **실 스킬은 반드시 clean 통과**.

## 주의
- 시그니처는 경로 스캔이 아니라 **라이브러리를 import**해서 얻는다 — 그래서 검사기는 현재 ops 라이브러리의 진짜 시그니처를 본다.
- land.sh의 스캔은 실제 `/srv/autophagy-skills/live`를 대상으로 하므로 `--sandbox-only`로 올라간 스냅샷의 스큐도 본다.

## 관련
- 검사기: `automation/skill_library_abi.py` (`check_snapshot`, `scan_live_root`, `SkipReason`)
- 배선: `automation/deploy-skill.sh` (`scan_live_skill_abi`), `automation/land.sh`
- 계기: 「승인 표면 단일화(AS) R3」의 ABI 스큐 후속 과제
- 테스트: `tests/unit/test_skill_library_abi_conformance.py`, `test_deploy_abi_scan.py`
- 증적: `docs/qa/DG-1/summary.txt`
