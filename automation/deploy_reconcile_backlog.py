"""Release-backlog digest wording kept separate from reconciliation timing.

백로그 시계와 통지 문구를 한 파일에 묶으면 설명 한 줄이 사고 시계의 LOC 한도를 밀어
넘긴다. 이 모듈은 문구만 맡아, 통지 주기와 에피소드 경계는 순수 상태 기계에 남긴다.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage, Periodic, Result


def release_backlog_notice(
    *,
    remote_head: str,
    current_sha: str,
    commit_count: int | None,
    elapsed: float,
    mirror_state: str,
) -> str:
    """동결된 관측 미러를 릴리스 미실행과 혼동하지 않도록 설명한다."""
    runtime = current_sha or "(none)"
    count = f"{commit_count}건" if commit_count is not None else "수 미상"
    mirror_notice = ""
    if mirror_state in {"dirty", "ahead"}:
        mirror_notice = (
            "\n관측 미러 `/srv/autophagy-agents`가 미커밋/미푸시 작업으로 동결되어 "
            "origin/main을 따라갈 수 없습니다.\n"
            "미러에서 `git format-patch` → 개발 체크아웃에서 적용 → commit/push 하세요; "
            "`git reset --hard`는 절대 사용하지 마세요."
        )
    elif mirror_state == "behind":
        mirror_notice = "\n관측 미러 `/srv/autophagy-agents`는 릴리스 후 origin/main을 따라갑니다."
    return (
        "릴리스 대기 중인 머지가 쌓여 있습니다 (머지=축적, 릴리스=배포).\n"
        f"  미배포 커밋 : {count} · {int(elapsed // 86400)}일 경과\n"
        f"  origin/main : {remote_head}\n"
        f"  runtime     : {runtime}\n"
        "릴리스하려면 워크스테이션에서 `automation/release.sh` 를 실행하세요 (소유자 ✅ 1회).\n"
        "노드는 서명 없는 head 를 설치하지 않으며, 이 상태는 사고가 아닙니다."
        f"{mirror_notice}"
    )


def backlog_message(content: str, detail: Periodic | Result) -> OwnerMessage | None:
    """새 통지와 durable 재시도가 같은 대상·릴리스 안내를 갖게 한다."""
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Ref
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    parsed = re.fullmatch(
        r"릴리스 대기 중인 머지가 쌓여 있습니다 \(머지=축적, 릴리스=배포\)\.\n"
        r"  미배포 커밋 : ([^\n]*)\n  origin/main : ([^\n]*)\n"
        r"  runtime     : ([^\n]*)\n.*", content, re.DOTALL,
    )
    if parsed is None:
        return None
    summary, head, runtime = parsed.groups()
    frozen = "`git format-patch`" in content
    owner_step = "워크스테이션에서 실행; 소유자 ✅ 1회"
    if frozen:
        owner_step = "먼저 git format-patch → 개발 체크아웃 적용·commit/push; reset --hard 금지; " + owner_step
    return OwnerMessage(
        subject_key=head, subject="릴리스 백로그",
        fact=f"미배포 {summary}; runtime {runtime}" + ("; 관측 미러 동결" if frozen else ""),
        location=Ref(scope="resource", search=("Git 커밋", head)),
        owner=Action("open", Ref(scope="resource", search=("명령", "automation/release.sh")), owner_step),
        agent_next="서명된 릴리스만 수렴; 미러 안전 판정 유지", recovery="not_applicable", detail=detail,
    )
