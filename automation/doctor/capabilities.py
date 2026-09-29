"""관측 → 기능별 판정. 설치 마법사의 「승인할 것」 미리보기와 doctor 가 같은 레지스트리를 읽는다.

판정은 셋이다: PASS(통과) · WARN(확인 — 선택 기능 미구성·부분 권한·판정 불가) ·
FAIL(고장 — 필수 연결이 없거나, 구성했는데 동작하지 않는다). 순수 로직이라 I/O 가 없다.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from automation.doctor import guides
from automation.doctor.facts import DRIVE_KEY, GWS_FILES, PATH_KEYS, TOKEN_KEY, Facts, GwsStatus, Role
from automation.install.checks import CheckResult, Status

CODEX: Final = "openai-codex"
CUSTOM_PREFIX: Final = "custom:"
MAILON_KEYS: Final = ("MAILON_ID", "MAILON_PW", "MAILON_TOTP_SECRET")
STREAK_FAIL: Final = 3
_G: Final = "https://www.googleapis.com/auth/"
GWS_SERVICES: Final = (
    ("캘린더", (f"{_G}calendar",)),
    ("메일 발송", (f"{_G}gmail.send", "https://mail.google.com/")),
    ("메일 읽기", (f"{_G}gmail.readonly", f"{_G}gmail.modify", "https://mail.google.com/")),
    ("Drive", (f"{_G}drive",)),
    ("과제비 시트", (f"{_G}spreadsheets",)),
    ("할 일", (f"{_G}tasks",)),
    ("회의 주소(Meet)", (f"{_G}meetings.space.created",)),
)


@dataclass(frozen=True, slots=True)
class Verdict:
    status: Status
    detail: str
    subjects: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Capability:
    key: str
    label: str
    roles: frozenset[Role]
    approve_at: str
    affects: str
    judge: Callable[[Facts], Verdict]


@dataclass(frozen=True, slots=True)
class Finding:
    key: str
    label: str
    status: Status
    detail: str
    affects: str
    steps: tuple[str, ...]
    subjects: tuple[str, ...] = ()


def _ok(detail: str) -> Verdict:
    return Verdict(Status.PASS, detail)


def _warn(detail: str, subjects: tuple[str, ...] = ()) -> Verdict:
    return Verdict(Status.WARN, detail, subjects)


def _fail(detail: str, subjects: tuple[str, ...] = ()) -> Verdict:
    return Verdict(Status.FAIL, detail, subjects)


def _hermes(f: Facts) -> Verdict:
    return _ok("~/.local/bin/hermes 있음") if f.hermes else _fail("Hermes 실행 파일이 없다")


def _gateway(f: Facts) -> Verdict:
    if f.gateway == "active":
        return _ok(f"{f.gateway_unit} 실행 중")
    if f.gateway == "unknown":
        return _warn(f"{f.gateway_unit} 상태를 알 수 없다(systemctl --user 에 닿지 못함)")
    return _fail(f"{f.gateway_unit} 가 {f.gateway} — 봇이 Discord 에 응답하지 않는다")


def _custom_name(provider: str) -> str | None:
    return provider.removeprefix(CUSTOM_PREFIX) if provider.startswith(CUSTOM_PREFIX) else None


def _primary(f: Facts) -> Verdict:
    provider = f.model_provider or CODEX
    custom = _custom_name(provider)
    if custom is not None:
        if custom in f.custom_providers:
            return _ok(f"{provider} — 사용자 정의 공급자라 로그인 대상이 아니다(연결 자체는 확인하지 않는다)")
        return _fail(f"{provider} 를 쓰도록 돼 있으나 custom_providers 에 {custom} 가 없다", (provider,))
    statuses = (f.auth or {}).get(provider, ())
    if not statuses:
        return _fail(f"{provider} 로그인이 없다 — 대화·요약·배치 모델 호출이 전부 실패한다", (provider,))
    if any(status in ("", "ok") for status in statuses):
        named = f"{provider}/{f.model_default}" if f.model_default else provider
        return _ok(f"주 모델 {named} — 로그인 {len(statuses)}건, 마지막 상태 정상")
    states = ", ".join(sorted(set(statuses)))
    return _fail(f"{provider} 로그인은 있으나 마지막 상태가 {states} — 다시 로그인한다", (provider,))


def _fallback(f: Facts) -> Verdict:
    if not f.fallback:
        return _warn("폴백 모델이 없다 — 주 모델이 막히면 곧바로 실패한다")
    missing = tuple(provider for provider in f.fallback
                    if _custom_name(provider) is None and not (f.auth or {}).get(provider))
    if missing:
        return _warn(f"폴백 {', '.join(missing)} 로그인이 없다 — 폴백이 실제로는 동작하지 않는다", missing)
    named = (
        [f"{provider}/{model}" for provider, model in zip(f.fallback, f.fallback_models, strict=True)]
        if len(f.fallback) == len(f.fallback_models) else list(f.fallback)
    )
    return _ok(f"폴백 {', '.join(named)} 로그인 있음")


def _by_design(result: CheckResult) -> bool:
    """설치 검사가 알리는 것 중 런타임에서는 결함이 아닌 둘을 뺀다.

    DM 승인 표면은 읽기 전용으로 확인할 수 없다는 선언일 뿐이고, 개인 서버 #approvals 는 id 가 없으면
    런타임이 채널 이름으로 찾는다(approval_directory 의 이름 검색 폴백) — 그래서 id 부재는 고장이 아니다.
    """
    return result.name.startswith("surface[") or (
        result.name == "channel[approvals]" and result.detail.startswith("MISSING-CHANNEL-ID")
    )


def _discord(f: Facts) -> Verdict:
    if f.secret_names is None:
        return _fail("~/.env.secrets 가 없다 — 봇 토큰을 둘 곳이 없다")
    if TOKEN_KEY not in f.secret_names:
        return _fail("DISCORD_BOT_TOKEN 이 비었다 — 봇이 접속하지 못한다")
    match f.discord:
        case None:
            return _ok("토큰 있음(온라인 검사 생략)")
        case str() as reason:
            return _warn(f"Discord API 로 확인하지 못했다: {reason}")
        case results:
            relevant = [result for result in results if not _by_design(result)]
            failed = [result for result in relevant if result.status is Status.FAIL]
            warned = [result for result in relevant if result.status is Status.WARN]
            if failed:
                detail = "; ".join(f"{result.name}: {result.detail[:140]}" for result in failed[:3])
                return _fail(detail, tuple(result.name for result in failed))
            if warned:
                return _warn("; ".join(f"{result.name}: {result.detail[:140]}" for result in warned[:3]),
                             tuple(result.name for result in warned))
            return _ok(f"토큰·인텐트·권한·채널 {len(relevant)}건 확인")


def _surface(f: Facts) -> Verdict:
    if f.interop is None:
        return _fail("~/.hermes/interop/config.json 이 없다 — 승인 카드를 게시할 곳이 없다")
    missing = tuple(key for key in ("agent_chat_channel_id", "owner_id") if key not in f.interop)
    if missing:
        return _fail(f"{', '.join(missing)} 가 비었다 — 승인 요청이 게시되지 않는다(fail-closed)", missing)
    if "owner_notice_channel_id" not in f.interop:
        return _warn("owner_notice_channel_id 가 비었다 — 정기 통지·doctor 알람이 DM 으로 간다")
    return _ok("승인 채널·소유자·통지 채널 설정됨")


def _gws(f: Facts) -> Verdict:
    if not f.gws_files:
        return _warn("미연결 — 메일 발송·캘린더·Drive·과제비·할 일 기능이 동작하지 않는다")
    if not f.gws_installed:
        return _fail("자격증명은 있으나 gws 실행 파일이 없다")
    absent = tuple(name for name in GWS_FILES if name not in f.gws_files)
    if absent:
        return _fail(f"자격증명 파일 없음: {', '.join(absent)} — Google 승인을 다시 옮긴다", absent)
    match f.gws:
        case None:
            return _ok("자격증명 파일 있음(토큰 검사 생략)")
        case str() as reason:
            return _fail(f"{reason} — 토큰을 확인하지 못했다")
        case GwsStatus(token_valid=False):
            return _fail("토큰이 유효하지 않다(만료·폐기) — Google 승인을 다시 한다")
        case GwsStatus(scopes=scopes):
            missing = tuple(name for name, accepted in GWS_SERVICES if not set(accepted) & set(scopes))
            if missing:
                return _warn(f"권한 없음: {', '.join(missing)} — 그 기능만 실패한다", missing)
            return _ok(f"토큰 유효, {len(GWS_SERVICES)}개 서비스 권한")


def _drive(f: Facts) -> Verdict:
    if f.drive_publish:
        return _ok(f"{DRIVE_KEY}=1 — 산출물을 Drive 에 발행한다")
    return _warn("꺼짐 — 회의록·문서 산출물이 Drive 에 올라가지 않고 로컬에만 남는다")


def _mailon(f: Facts) -> Verdict:
    have = tuple(key for key in MAILON_KEYS if key in (f.secret_names or frozenset()))
    if not have:
        return _warn("미구성 — 기관메일을 쓰지 않으면 무시해도 된다")
    missing = tuple(key for key in MAILON_KEYS if key not in have)
    if missing:
        return _fail(f"일부만 설정됨, 빠진 값: {', '.join(missing)}", missing)
    return _ok("기관메일 자격증명 3종 있음")


def _stt(f: Facts) -> Verdict:
    if not f.paths:
        return _warn("미구성 — 로컬 음성 전사(whisper.cpp) 경로가 없다")
    broken = tuple(key for key, exists in f.paths.items() if not exists)
    if broken:
        return _fail(f"설정된 경로가 없다: {', '.join(broken)}", broken)
    unset = tuple(key for key in PATH_KEYS if key not in f.paths)
    if unset:
        return _warn(f"설정 안 됨: {', '.join(unset)}", unset)
    return _ok("whisper.cpp 실행 파일·모델 있음")


def _cron(f: Facts) -> Verdict:
    if f.cron is None:
        return _warn("정기 작업 목록이 없다 — 워처가 아직 배포되지 않았다")
    active = [job for job in f.cron if job.enabled and not job.paused]
    failing = [job for job in active if job.last_status == "error"]
    broken = [job for job in failing if job.failure_streak >= STREAK_FAIL]
    if broken:
        detail = "; ".join(f"{job.name}({job.failure_streak}회 연속: {job.last_error})" for job in broken)
        return _fail(f"{len(broken)}개 반복 실패 — {detail}", tuple(job.name for job in broken))
    stopped = [job for job in f.cron if not job.enabled or job.paused]
    if failing or stopped:
        names = tuple(job.name for job in (*failing, *stopped))
        return _warn(f"최근 실패·중지: {', '.join(names)}", names)
    return _ok(f"{len(active)}개 정상")


_BOTH: Final[frozenset[Role]] = frozenset(("agent", "peer"))
_AGENT: Final[frozenset[Role]] = frozenset(("agent",))

CAPABILITIES: Final = (
    Capability("hermes-cli", "Hermes 런타임", _BOTH, "이 계정에 Hermes 설치", "에이전트 전체", _hermes),
    Capability("hermes-gateway", "Hermes 게이트웨이", _BOTH, "", "Discord 대화·승인 버튼", _gateway),
    Capability("primary-model", "주 모델(~/.hermes/config.yaml 의 model:)", _BOTH,
               "ChatGPT 구독 계정의 기기 코드 승인", "모든 대화·요약·배치 모델 호출", _primary),
    Capability("model-fallback", "폴백 모델 로그인", _BOTH,
               "xAI 계정의 기기 코드 승인(선택)", "주 모델이 막혔을 때의 대체 응답", _fallback),
    Capability("discord-bot", "Discord 봇", _BOTH,
               "Discord Developer Portal(토큰·Message Content 인텐트)과 개인 서버 초대",
               "대화·승인 카드·결과 통지 전체", _discord),
    Capability("approval-surface", "승인 채널", _AGENT,
               "개인 서버 #agent-chat·#notifications 채널 id 와 소유자 id",
               "메일·캘린더·과제비·할 일 등 모든 승인 요청", _surface),
    Capability("google-workspace", "Google Workspace", _AGENT,
               "Google 동의 화면(OAuth) — 캘린더·Gmail·Drive·Sheets·Tasks",
               "메일 발송·캘린더·Drive 발행·과제비·할 일", _gws),
    Capability("drive-publish", "Drive 산출물 발행", _AGENT,
               "~/.env.secrets 의 DRIVE_PUBLISH_ENABLED=1", "회의록·문서가 Drive 에 올라가는 것", _drive),
    Capability("institutional-mail", "기관메일(MailOn, 선택)", _AGENT,
               "기관 계정 ID·비밀번호·OTP 비밀", "기관메일 조회·발송", _mailon),
    Capability("speech-to-text", "로컬 음성 전사", _AGENT,
               "whisper.cpp 설치와 모델 경로", "녹음 → 전사본 → 회의록", _stt),
    Capability("scheduled-jobs", "정기 작업(워처)", _BOTH, "",
               "승인 리액션 처리·리마인더·일일 보고 등 백그라운드 작업", _cron),
)


def evaluate(facts: Facts) -> tuple[Finding, ...]:
    home = str(facts.home)
    findings: list[Finding] = []
    for capability in CAPABILITIES:
        if facts.role not in capability.roles:
            continue
        verdict = capability.judge(facts)
        steps = () if verdict.status is Status.PASS else guides.steps(
            capability.key, account=facts.account, home=home, unit=facts.gateway_unit,
        )
        findings.append(Finding(capability.key, capability.label, verdict.status, verdict.detail,
                                capability.affects, steps, verdict.subjects))
    return tuple(findings)
