# AUTO-GENERATED

자동 생성 감지기는 원문 작업을 저장하지 않고 정규화한 SHA-256 패턴 해시만 기록한다.
동일 ISO 주에 동일 패턴이 3회 관측되면 `auto-<hash16>` 초안을 만들며, 초안은
`automation/skill_generation/dispatch.sh`를 통해서만 W1-8 파이프라인에 전달된다.
운영 중 갱신되는 원장은 `~agent/.hermes/skill-generation/AUTO-GENERATED.md`에
0600으로 유지하며, 아래 파일은 검토 가능한 마스킹된 VCS 대장이다.

| UTC | Skill | Trigger | Status | Pipeline |
| --- | --- | --- | --- | --- |
| 2026-07-16 | `auto-589d5fdea190fcee` | `sha256:589d5fdea190fcee` | MOUNTED (isolated E2E) | W1-8 sandbox → signed owner approval → mount |
| 2026-07-16 | `auto-eda6649be17ba3b8` | `sha256:eda6649be17ba3b8` | AWAITING-OWNER (isolated E2E) | W1-8 sandbox → approval request |
| 2026-07-16 | `auto-2d847f7d7f9d0353` | `sha256:2d847f7d7f9d0353` | AWAITING-OWNER (isolated E2E) | W1-8 sandbox → approval request |
| 2026-07-16 | `auto-f24764e917437815` | `sha256:f24764e917437815` | AUTO-HELD | W1-8 exit 3 weekly cap |
| 2026-07-16 | `auto-bypassed` | `sha256:not-recorded` | BYPASS-REJECTED | runtime mount audit removed unmanaged copy |
