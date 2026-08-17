# Hermes `_find_skill`이 심링크 팜 스킬 루트를 보지 못한다 (upstream 보고용)

**대상**: Hermes v0.18.2 · `tools/skill_manager_tool.py`
**영향**: 승인 게이트를 강제하는 관리자 배포 스킬이 에이전트 자가 저작 스킬에 **가려질 수 있다**
**상태**: 우리 쪽은 탐지(`SHADOWS-GOVERNED`)로 버티는 중. 예방은 upstream 수정에 달려 있다.

## 증상

`skill_manage(create)`는 같은 이름의 스킬이 이미 있으면 거부한다. 그 판정은 `_find_skill`이
하는데, 스킬 루트를 `rglob("SKILL.md")`로 훑는다. **파이썬의 `rglob`은 디렉터리 심링크를
따라가지 않는다.**

그래서 `skills.external_dirs`로 등록한 루트가 릴리스로 가는 **심링크 팜**이면, 그 안의 스킬이
한 건도 보이지 않는다.

## 실측 (2026-08-16)

```
/srv/autophagy-skills/live/          ← skills.external_dirs 로 등록된 read-only 루트
  mail    -> /srv/autophagy-skill-store/<digest>/mail      (디렉터리 심링크)
  recall  -> /srv/autophagy-skill-store/<digest>/recall
  ... (17종)

_find_skill("mail")   → None
_find_skill("recall") → None
```

두 스킬 모두 디스크에 정상 존재하고 `hermes skills list`에도 나오는데, 충돌 검사만 못 본다.
실제로 에이전트가 `recall` 이름의 자가 스킬을 만드는 데 성공했다(즉시 제거).

## 왜 심각한가

1차 루트(계정 소유 `~/.hermes/skills`)가 발견 우선순위에서 이긴다. 따라서 위 상태에서 만들어진
자가 스킬은 **관리자 배포본을 가린다**. 배포본이 강제하던 소유자 승인 게이트가 그 이름에 대해
무력화되고, 아무 오류도 나지 않는다.

## 제안 수정

`_find_skill`의 순회를 심링크를 따라가도록 바꾼다:

```python
# 현재
for path in root.rglob("SKILL.md"): ...

# 제안
for dirpath, _dirnames, filenames in os.walk(root, followlinks=True):
    if "SKILL.md" in filenames: ...
```

`os.walk(followlinks=True)`는 심링크 순환에 취약하므로, 방문한 `st_dev`/`st_ino` 집합으로
재방문을 막는 가드를 함께 두는 것이 안전하다.

## 우회 (수정 전까지)

- 탐지: 자기 루트 이름과 governed 루트 이름을 대조해 겹치면 소유자에게 즉시 알린다
  (`automation/selfskill_audit` — 델타가 없어도 보고).
- 반대 방향(배포가 자가 저작물을 덮어씀)은 `deploy-skill.sh`가 `SELF-SKILL-COLLISION-BLOCK`으로 막는다.
- 근본 예방(live 루트를 실디렉터리로 전환)은 비용이 크다 — 심링크 팜을 만드는 주체가 root NOPASSWD
  헬퍼이고, 이 저장소의 배포 판정 자체가 `readlink live/<skill>` 해시라 그 기전이 사라진다.
