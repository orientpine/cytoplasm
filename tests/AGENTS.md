# tests/ — 단위 + E2E

루트에서 실행: `pytest tests/unit` (루트 pytest 설정 없음 — 기본값 + `.pytest_cache`).
`configs/rag/*` 서비스는 자체 `pyproject.toml`의 pytest를 별도로 가진다(`tests/**`는 S101 제외).

## 구조
```
tests/
├── unit/                # 순수 로직·게이트·상태머신 단위 테스트 (56+)
└── e2e/
    ├── drivers/         # 게이트·피어·훅 경계를 구동하는 드라이버
    ├── scenarios/       # 종단 시나리오 스크립트
    └── fixtures/        # 고정 입력 (예: w2-personal-memory)
```

## e2e 규약
- scenario YAML(`scenarios/*.yaml`)은 `cases`/`expect` 스키마. driver/actor가 `OBS-JSON` 한 줄을 출력하고 `drivers/judge_expectations.py`가 exact-equality로 판정.
- fixture는 `e2e/fixtures/<시나리오>/`에 시나리오 전용 자산(입력 md + 기록된 LLM 응답 JSON 등).
- `conftest.py` 없음 — unit은 `tmp_path`/`monkeypatch`/stub 실행파일/env override 패턴을 직접 사용.

## 규칙
- **FS3 정산 레코드가 고정한 테스트 파일에는 케이스를 더하지 않는다.** `.omo/evidence/fs3/completions/task-*.json` 의 `green`/`red` 는 특정 테스트 파일들의 **출력 해시**를 못박고 `tests/unit/test_fs3_replay_gate.py` 가 매번 재생을 대조한다 — 그 파일에 한 줄만 더해도 과거 RED/GREEN 증적이 재현되지 않는다(2026-08-26 실측: `test_watch_failure_streak.py`·`test_deploy_host_fail_closed.py`). 원장의 해시를 고쳐 맞추는 것은 증적 위조다. 새 검사는 **새 파일**에 두고 왜 갈라놨는지를 그 파일 docstring 에 적는다(선례: `test_watch_failure_streak_store.py`, `test_deploy_host_fail_closed_all.py`).
- **버그 수리는 RED→GREEN 회귀 고정 선행.** 특히 no-agent cron의 자식 subprocess 자격증명 전파는
  회귀로 못박는다 — 선례: `test_calendar_confirm_watch_subprocess.py`,
  `test_coordination_confirm_watch_subprocess.py` (부모 env에 토큰 없고 accessor만 해석 가능한 상황에서 자식이 토큰을 받는지 검증).
- 게이트/보안 경계(`external_effect_gate`, 승인 판정, deadlock/재협상)는 mutation 로직 변경 시 반드시 단위 테스트 동반.
- 테스트는 실시크릿·실외부효과를 유발하지 않는다 — 격리(`E2E_TEST_MODE`/DUMMY 시크릿/서명 주입) 경로만 사용.
