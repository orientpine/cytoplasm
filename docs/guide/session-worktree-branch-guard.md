# 세션 워크트리 브랜치 가드

GitHub private/free 저장소에서는 `main` 브랜치 보호를 켤 수 없다. 이 저장소는 세션 작업을
PR로 반영한다는 규율을 보완하기 위해, **linked worktree에서 `refs/heads/main`으로 향하는
push만** 로컬 `pre-push` 훅으로 거부한다.

## 설치와 판정

- `automation/worktree.sh start <name>`을 실행할 때마다
  `automation/hooks/pre-push`를 Git common directory의 `hooks/pre-push` 한 경로에
  `install(1)`로 교체한다. 모든 linked worktree가 common directory를 공유하므로 한 번의
  설치가 현재와 미래의 모든 세션에 적용된다.
- 훅은 표준 pre-push stdin의 `remote-ref`를 읽는다. 현재 checkout의 `.git`이
  `gitdir: .../.git/worktrees/<name>` 형태의 파일이고 `remote-ref`가 정확히
  `refs/heads/main`일 때만 실패한다.
- 메인 checkout은 `.git`이 디렉터리이므로 통과한다. 따라서 메인 checkout에서 실행되는
  `automation/land.sh`의 `git push origin main`은 영향을 받지 않는다.
- 세션 브랜치, repair 브랜치, tag push는 remote ref가 main이 아니므로 통과한다. 다른
  저장소에서 동작하는 Obsidian writer도 이 저장소의 shared hook과 무관하다.
- 환경변수 탈출구는 없다. 이 훅은 원격 브랜치 보호를 대신하는 보안 경계가 아니라,
  세션이 PR 단계를 실수로 건너뛰는 것을 즉시 막는 로컬 규율 가드다.

## 실패했을 때

세션 워크트리에서 main push가 거부되면 변경을 버리거나 훅을 우회하지 않는다. 현재
`session/<name>` 브랜치를 push하고 main 대상 PR을 만든다. main 착지는 cha의 GitHub 리뷰와
merge 뒤 별도 흐름이 담당한다.

회귀 테스트는 실제 저장소의 shared hook을 건드리지 않고 `/tmp` 아래 bare origin + main
checkout + linked worktree를 만들어 설치 멱등성, 세션 main push 거부, 메인 checkout 통과를
함께 검증한다.
