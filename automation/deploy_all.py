"""배포 전량 판정의 순수 로직 (RC-3/4) — 관측을 받아 계획·행동·영수증을 만든다.

"origin/main 의 모든 것이 prod 에 있는가"는 지금까지 표면별 프로브 넷에 흩어져 있었고
한 번에 묻는 판정이 없었다 — 그래서 스킬 6개 중 2개만 배포된 상태와 전부 배포된 상태가
명령 수준에서 구분되지 않았다(계획 C3, 2026-08-01 실측 2일 침묵). 이 모듈은 노드 관측
(`deploy_all_probe`)을 받아 그 질문에 하나의 답을 낸다: 계획(무엇이 낡았나) ·
행동(어느 배포기를 부르나) · 영수증(전량 반영이 검증된 릴리스 sha).

I/O 가 없다 — 관측 줄을 받아 판정을 돌려줄 뿐이다. 관측은 노드에서
`automation/deploy_all_probe.py` 가, 실행은 `automation/deploy_all.sh` 가 소유한다.
영수증은 clean 일 때만 만들어진다 — 부분 성공을 성공으로 보고하지 않는 것이 이 모듈의
존재 이유다.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

RECEIPT_VERSION: Final = 1

#: 표면 ⑤(root 자산)·⑥(RAG)·hermes 런타임 패키지는 이 영수증이 직접 판정하지 않는다 —
#: 상시 healthcheck 프로브(release_helper_drift · rag_stack_current ·
#: runtime_packages_current)가 릴리스와 무관하게 계속 대조한다. 여기 적는 이유는
#: 영수증을 읽는 쪽이 "전량"의 경계를 오해하지 않게 하기 위해서다.
DELEGATED_SURFACES: Final = ("release-helpers", "runtime-packages", "rag-stack")


class ObservationError(RuntimeError):
    """관측이 불완전하거나 기형이다 — 잘린 관측을 깨끗함으로 읽으면 안 된다."""


@dataclass(frozen=True, slots=True)
class HomeState:
    account: str
    destination: str
    source: str
    policy: str  # "required" | "optional"
    release_sha: str
    deployed_sha: str  # "" = 없음 · "?" = 읽지 못함

    @property
    def owning_package(self) -> str:
        return "/".join(self.source.split("/")[:2])

    @property
    def status(self) -> str:
        if self.deployed_sha == "?":
            return "unknown"
        if not self.deployed_sha:
            return "ok-absent" if self.policy == "optional" else "absent"
        return "ok" if self.deployed_sha == self.release_sha else "stale"


@dataclass(frozen=True, slots=True)
class Plan:
    release_sha: str
    mount_stale: tuple[tuple[str, str, str], ...]
    mount_unmounted: tuple[str, ...]
    mount_orphaned: tuple[str, ...]
    home: tuple[HomeState, ...]

    @property
    def home_defects(self) -> tuple[HomeState, ...]:
        return tuple(s for s in self.home if s.status in ("stale", "absent", "unknown"))

    @property
    def skills_to_deploy(self) -> tuple[str, ...]:
        names = {name for name, _, _ in self.mount_stale} | set(self.mount_unmounted)
        return tuple(sorted(names))

    @property
    def packages_to_deploy(self) -> tuple[str, ...]:
        return tuple(
            sorted({
                state.owning_package
                for state in self.home_defects
                if state.status in ("stale", "absent")
            })
        )

    @property
    def gateway_restart_needed(self) -> bool:
        """플러그인은 게이트웨이 프로세스 시작 시 로드된다 — 파일 배포만으로는 반영이 아니다."""
        return any(s.destination.startswith(".hermes/plugins/") for s in self.home_defects)

    @property
    def clean(self) -> bool:
        return not (
            self.mount_stale or self.mount_unmounted or self.mount_orphaned or self.home_defects
        )


def parse_observations(lines: Iterable[str]) -> Plan:
    """관측 줄을 엄격하게 접는다. `OBS|end` 가 없으면 잘린 것이고, 잘린 것은 판정이 아니다."""
    release_sha = ""
    mounts_judged = False
    ended = False
    mount_stale: list[tuple[str, str, str]] = []
    mount_unmounted: list[str] = []
    mount_orphaned: list[str] = []
    home: list[HomeState] = []
    for raw in lines:
        line = raw.strip()
        if not line.startswith("OBS|"):
            continue  # ssh 전송에 섞인 비관측 줄은 판정에 쓰지 않는다
        parts = line.split("|")
        kind = parts[1]
        if kind == "release" and len(parts) == 3 and parts[2]:
            release_sha = parts[2]
        elif kind == "mounts" and len(parts) == 3 and parts[2] == "judged":
            mounts_judged = True
        elif kind == "mount-stale" and len(parts) == 5:
            mount_stale.append((parts[2], parts[3], parts[4]))
        elif kind == "mount-unmounted" and len(parts) == 3:
            mount_unmounted.append(parts[2])
        elif kind == "mount-orphaned" and len(parts) == 3:
            mount_orphaned.append(parts[2])
        elif kind == "home" and len(parts) == 8:
            home.append(HomeState(parts[2], parts[3], parts[4], parts[5], parts[6], parts[7]))
        elif kind == "end" and len(parts) == 2:
            ended = True
        else:
            raise ObservationError(f"malformed observation: {line[:80]}")
    if not ended:
        raise ObservationError("truncated observations: OBS|end is missing")
    if not release_sha:
        raise ObservationError("no release sha observed")
    if not mounts_judged:
        raise ObservationError("mounts were not judged")
    if not home:
        raise ObservationError("no home-artifact observations")
    return Plan(
        release_sha=release_sha,
        mount_stale=tuple(mount_stale),
        mount_unmounted=tuple(mount_unmounted),
        mount_orphaned=tuple(mount_orphaned),
        home=tuple(home),
    )


def render_plan(plan: Plan) -> str:
    header = f"DEPLOY-ALL: release {plan.release_sha[:16]}"
    if plan.clean:
        return f"{header} — 전량 일치, 할 일 없음"
    lines = [header]
    for skill, expected, mounted in plan.mount_stale:
        lines.append(
            f"  SKILL-STALE {skill}: 릴리스 {expected[:12]}… / 마운트 {mounted[:12]}…"
            f" → deploy-skill.sh {skill}"
        )
    for skill in plan.mount_unmounted:
        lines.append(f"  SKILL-UNMOUNTED {skill} → deploy-skill.sh {skill}")
    for skill in plan.mount_orphaned:
        lines.append(f"  SKILL-ORPHANED {skill} — 릴리스에 없음(수동 판단: --remove)")
    for state in plan.home_defects:
        if state.status == "unknown":
            lines.append(
                f"  HOME-UNKNOWN {state.account}:{state.destination} — 읽지 못함(fail-closed)"
            )
        else:
            lines.append(
                f"  HOME-{state.status.upper()} {state.account}:{state.destination}"
                f" → {state.owning_package}/deploy.sh"
            )
    if plan.gateway_restart_needed:
        lines.append("  GATEWAY-RESTART 필요: 플러그인 갱신은 agent+peer 재시동까지가 반영이다")
    return "\n".join(lines)


def render_actions(plan: Plan) -> str:
    """오케스트레이터가 기계로 읽는 행동 목록. clean 이면 빈 문자열이다."""
    lines: list[str] = []
    for skill in plan.skills_to_deploy:
        lines.append(f"ACT|deploy-skill|{skill}")
    for package in plan.packages_to_deploy:
        lines.append(f"ACT|run-deployer|{package}/deploy.sh")
    if plan.gateway_restart_needed:
        lines.append("ACT|restart-gateway|agent+peer")
    for skill in plan.mount_orphaned:
        lines.append(f"ACT|manual|orphaned-mount:{skill}")
    for state in plan.home_defects:
        if state.status == "unknown":
            lines.append(f"ACT|manual|unreadable:{state.account}:{state.destination}")
    return "\n".join(lines)


def render_receipt(plan: Plan, *, verified_at: str) -> str:
    """전량 반영 영수증 — clean 이 아닌 계획에 서명하는 것을 코드가 거부한다."""
    if not plan.clean:
        raise ObservationError("refusing to attest a non-clean deployment")
    payload = {
        "version": RECEIPT_VERSION,
        "release_sha": plan.release_sha,
        "verified_at": verified_at,
        "surfaces": {
            "skill_mounts": "ok",
            "home_artifacts": {
                "ok": sum(1 for s in plan.home if s.status == "ok"),
                "ok_absent_optional": sum(1 for s in plan.home if s.status == "ok-absent"),
            },
        },
        "delegated": list(DELEGATED_SURFACES),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
