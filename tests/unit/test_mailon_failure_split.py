"""mailon exit 2 를 인증 실패와 브라우저 실패로 가른다 — 5일 오진의 실제 원인.

2026-08-13~18 소유자의 논문 메일이 막힌 5일 동안 원인은 계속 「기관메일 인증 실패」로
보고됐다. 실제 원인은 로그인 완료 오판(PR #148)과 메일함 SPA 기동 경쟁(PR #152)이었고,
자격증명은 멀쩡했다. 오진의 기전은 단순하다 — mailon 의 exit 2 는 `auth_or_browser_error`,
즉 **두 부류를 한 코드로 접은** 값인데, 래퍼가 그것을 조건 없이 `auth_error` 로 접고
"기관메일 인증 실패" 라고 **단정**하는 안내문을 붙였다. 그래서 수리 방향이 반대로 돌았고
비밀번호 교체 권고까지 갔다.

시그니처는 이미 있었다(`classify_stderr`). 쓰지 않았을 뿐이다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "mail" / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(_SCRIPTS))
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("signature", "expected"),
    (
        ("login_error", "auth_error"),
        ("login_dom_ipt_id", "auth_error"),
        ("browser_error", "browser_error"),
        ("timeout", "browser_error"),
        ("inbox_folder_uid_selector", "browser_error"),
    ),
)
def test_exit_two_is_split_by_the_signature_that_already_existed(
    signature: str, expected: str
) -> None:
    failure = _load("mailon_failure")

    # When: rc 2 arrives with a stderr signature the wrapper already classifies.
    code, guidance = failure.classify_exit_two(signature)

    # Then: authentication and browser failures no longer share one verdict.
    assert code == expected
    assert guidance.strip()


def test_an_unclassified_exit_two_refuses_to_assert_authentication_failure() -> None:
    failure = _load("mailon_failure")

    # When: nothing in stderr identifies which half of exit 2 this is.
    code, guidance = failure.classify_exit_two("unclassified")

    # Then: the wrapper says it does not know instead of blaming the credentials —
    # asserting auth is exactly what turned the repair around for five days.
    assert code == "auth_or_browser_error"
    assert not guidance.startswith("기관메일 인증 실패")
    assert "단정하지 말 것" in guidance


def test_the_browser_guidance_steers_away_from_the_credential_path() -> None:
    failure = _load("mailon_failure")

    _, guidance = failure.classify_exit_two("browser_error")

    # Then: it names the cause it has evidence for and explicitly blocks the detour that
    # cost five days — the guidance never opens by declaring an authentication failure.
    assert not guidance.startswith("기관메일 인증 실패")
    assert "재인증·비밀번호 교체로 가지 말 것" in guidance


def test_reauth_guidance_points_at_the_runtime_that_actually_exists() -> None:
    interface = _load("mailon_interface")

    # Then: `orientpine/emailAutomation` was absorbed on 2026-07-30; the manual
    # verification step must name the runtime the node really has, because this text
    # is only ever read while an incident is already in progress.
    assert "emailAutomation" not in interface.REAUTH_GUIDANCE
    assert ".hermes/mailon-runtime/current" in interface.REAUTH_GUIDANCE


def test_every_exit_two_code_still_leaves_the_process_exit_code_at_two() -> None:
    interface = _load("mailon_interface")
    failure = _load("mailon_failure")

    # Then: callers that branch on the process exit code see no change; only the
    # machine-readable error_code and the human guidance got more precise.
    for signature in ("login_error", "browser_error", "unclassified"):
        code, _ = failure.classify_exit_two(signature)
        assert interface.WRAPPER_EXIT[code] == 2


def test_resolve_candidates_are_ranked_deterministically(tmp_path: Path) -> None:
    read = _load("mail_wrapper_read")
    candidates = [
        {"group": "history", "name": "김샘플", "email": "old@example.invalid", "org": ""},
        {"group": "contacts", "name": "김샘플", "email": "now@example.invalid", "org": ""},
        {"group": "organization", "name": "김샘플", "email": "now@example.invalid", "org": "AX"},
    ]

    ranked = read.rank_candidates(candidates)

    # Then: 실측된 순서 없음 문제를 결정론으로 닫는다 — organization > contacts > history.
    assert [item["group"] for item in ranked] == ["organization", "contacts", "history"]


def test_multiple_addresses_are_reported_as_ambiguous_rather_than_silently_picked() -> None:
    read = _load("mail_wrapper_read")
    candidates = [
        {"group": "organization", "name": "김샘플", "email": "now@example.invalid", "org": "AX"},
        {"group": "contacts", "name": "김샘플", "email": "now@example.invalid", "org": ""},
        {"group": "history", "name": "김샘플", "email": "old@example.invalid", "org": ""},
    ]

    # Then: 3 candidates but 2 addresses — the caller must be told, because picking the
    # wrong one is an irreversible external effect and the approval gate is the last
    # line of defence, not a decision procedure.
    assert read.distinct_addresses(candidates) == 2
    assert read.distinct_addresses(candidates[:2]) == 1


def test_an_unknown_group_sorts_last_instead_of_crashing() -> None:
    read = _load("mail_wrapper_read")
    candidates = [
        {"group": "mystery", "name": "n", "email": "a@example.invalid", "org": ""},
        {"group": "organization", "name": "n", "email": "b@example.invalid", "org": ""},
    ]

    ranked = read.rank_candidates(candidates)

    assert [item["group"] for item in ranked] == ["organization", "mystery"]
