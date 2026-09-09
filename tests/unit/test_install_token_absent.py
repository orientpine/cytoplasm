"""토큰이 없는 것은 판정 불가이지, 판정해서 나온 실패가 아니다.

`automation/install/executor.py` 는 `discord_check.py` 의 종료코드를
``PASS if code == 0 else FAIL`` 로 접었다. 그런데 그 스크립트는 **서로 다른 두 가지**를
rc 로 구분한다 — rc=1 은 "검사했고 전제가 충족되지 않았다"이고, rc=2 는
"``DISCORD_BOT_TOKEN`` 이 환경에 없어 아무것도 검사하지 못했다"이다
(`automation/install/discord_check.py` 의 ``main``).

둘을 접으면 `automation/install/apply.py` 의 ``apply_plan`` 이 FAIL 에서 break 하고,
그 체크는 계획 앞쪽에 있으므로(`automation/install/plan.py` 의 ``build_plan``)
**계획의 나머지 전부**가 실행되지 않는다: 배포키 생성·등록 안내·gitleaks·체크아웃·
자산 파일·심링크·healthcheck 프로브·타이머·신뢰키 검증·최종 healthcheck. 그런데
`docs/guide/install.md` §6 은 바로 그 경로(``sudo`` 가 환경변수를 지운 설치)를 명시적으로
허용한다 — "Discord 전제는 설치 자체의 전제가 아니다". 산문이 맞고 코드가 틀렸다.
2026-09-07 외부 설치자가 밟은 자리다.

그래서 rc=2 는 WARN 이다. `automation/install/checks.py` 의 ``exit_code`` 는 WARN 으로
종료코드를 바꾸지 않으므로 종료 게이트는 그대로 엄격하고, 설치기가 판정하지 못한 항목은
종료 출력의 「소유자 확인 절차」가 사람에게 넘긴다.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Final

import pytest

from automation.install import executor as executor_module
from automation.install.apply import apply_plan
from automation.install.assets import build_inputs
from automation.install.checks import CheckResult, Status, exit_code
from automation.install.executor import ExecutionContext, RealExecutor
from automation.install.plan import (
    Check,
    InstallAction,
    InstallPlan,
    SystemState,
    build_plan,
)
from automation.node_config import default_node_config

_REPO: Final = Path(__file__).resolve().parents[2]
_DISCORD_CONFIG: Final = Path("/home/agent/.hermes/interop/config.json")
_TOKEN_ABSENT: Final = 2


def _update_trust_key() -> str:
    algorithm = b"ssh-ed25519"
    blob = len(algorithm).to_bytes(4, "big") + algorithm
    blob += (32).to_bytes(4, "big") + bytes(range(32))
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} token-absent"


def _executor(monkeypatch: pytest.MonkeyPatch, code: int) -> RealExecutor:
    """A real executor whose only injected part is what `discord_check.py` answered."""
    context = ExecutionContext(default_node_config(), _REPO, _DISCORD_CONFIG, None)
    monkeypatch.setattr(executor_module, "discord_check_main", lambda _argv: code)
    return RealExecutor(context)


class _StatusExecutor:
    """Answers each action with a fixed status so `apply_plan`'s own rule is what is tested."""

    def __init__(self, statuses: dict[str, Status]) -> None:
        self.statuses = statuses
        self.executed: list[InstallAction] = []

    def execute(self, action: InstallAction) -> tuple[CheckResult, ...]:
        self.executed.append(action)
        name = action.name if isinstance(action, Check) else type(action).__name__
        return (CheckResult(name, self.statuses.get(name, Status.PASS), "test result"),)


# --------------------------------------------------------------------------- #
# rc=2 — the installer could not decide.
# --------------------------------------------------------------------------- #
def test_an_absent_token_warns_rather_than_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an installer whose environment has no bot token, which is what `sudo` leaves
    # behind and what docs/guide/install.md §6 tells the operator they may accept.
    executor = _executor(monkeypatch, _TOKEN_ABSENT)

    # When: the readiness check runs.
    (result,) = executor.execute(Check("discord-readiness"))

    # Then: it is a warning about an undecided prerequisite, not a verdict of failure.
    assert result.status is Status.WARN
    assert "TOKEN-ABSENT" in result.detail
    assert "DISCORD_BOT_TOKEN" in result.detail


def test_an_absent_token_warns_without_changing_the_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the same undecided check.
    executor = _executor(monkeypatch, _TOKEN_ABSENT)

    # When: the installer scores the run.
    results = executor.execute(Check("discord-readiness"))

    # Then: the exit gate stays exactly as strict as before — a warning is not a failure,
    # so relaxing this check cannot make a genuinely broken install report success.
    assert exit_code(results) == 0


def test_an_absent_token_warns_and_the_rest_of_the_install_still_runs() -> None:
    # Given: the readiness check sitting where build_plan puts it — before everything else.
    plan = InstallPlan(
        (Check("hermes-gateway"), Check("discord-readiness"), Check("healthcheck"))
    )
    executor = _StatusExecutor({"discord-readiness": Status.WARN})

    # When: the plan is applied.
    results = apply_plan(plan, executor)

    # Then: the actions behind it still run. This is the whole point — the fold used to
    # cost the operator the entire remainder of the install.
    assert executor.executed == list(plan.actions)
    assert [result.status for result in results] == [Status.PASS, Status.WARN, Status.PASS]


def test_most_of_the_install_really_is_planned_after_the_discord_check() -> None:
    # Given: the plan a fresh node builds.
    inputs = build_inputs(_REPO, default_node_config(), _update_trust_key())
    actions = build_plan(inputs, SystemState.empty()).actions

    # When: the readiness check's position is read.
    index = actions.index(Check("discord-readiness"))

    # Then: it is early, so folding rc=2 into FAIL blocks the rest. Without this the two
    # tests above would pass while proving nothing — move the check to the end and the
    # bug disappears without the fix.
    assert len(actions) - index > 10, actions
    for later in (Check("deploy-key-registration"), Check("healthcheck")):
        assert actions.index(later) > index


# --------------------------------------------------------------------------- #
# rc=1 — the installer decided, and the answer was no.
# --------------------------------------------------------------------------- #
def test_an_argv_the_checker_refuses_is_a_usage_failure_not_a_token_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: `discord_check.py` refusing its argv. argparse does not return — it calls
    # `parser.error`, which raises `SystemExit`, and `RealExecutor.execute` catches only
    # OSError / CalledProcessError / TrustKeyError, so it escaped as a traceback instead
    # of a named verdict. A config path beginning with `-` reaches this.
    context = ExecutionContext(default_node_config(), _REPO, _DISCORD_CONFIG, None)

    def refuse(_argv: object) -> int:
        raise SystemExit(2)

    monkeypatch.setattr(executor_module, "discord_check_main", refuse)
    executor = RealExecutor(context)

    # When: the check runs.
    (result,) = executor.execute(Check("discord-readiness"))

    # Then: a named FAIL — and NOT the token-absent warning. `SystemExit(2)` and the
    # checker's own rc=2 are the same number, so mapping the exit status through the
    # verdict table would relabel "the installer built a bad command" as "the operator's
    # token is missing" and let the install continue on a false story.
    assert result.status is Status.FAIL
    assert "USAGE-REFUSED" in result.detail
    assert "TOKEN-ABSENT" not in result.detail


def test_a_decided_failure_still_blocks_the_install(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a token that reached the installer and a prerequisite that did not hold.
    executor = _executor(monkeypatch, 1)

    # When: the readiness check runs.
    (result,) = executor.execute(Check("discord-readiness"))

    # Then: it fails, in the wording docs/guide/install.md §8 quotes verbatim.
    assert result.status is Status.FAIL
    assert result.detail == "discord_check.py rc=1"
    assert exit_code((result,)) == 1

    # And: apply_plan still stops there, so relaxing rc=2 did not also relax rc=1 —
    # asserted rather than asserted-in-a-comment, because this half is the guarantee
    # that the whole change rests on.
    blocking = _StatusExecutor({"discord-readiness": Status.FAIL})
    blocked = apply_plan(
        InstallPlan((Check("discord-readiness"), Check("healthcheck"))), blocking
    )
    assert blocking.executed == [Check("discord-readiness")]
    assert exit_code(blocked) == 1


@pytest.mark.parametrize(("code", "status"), ((0, Status.PASS), (1, Status.FAIL)))
def test_the_decided_verdicts_keep_their_documented_detail(
    monkeypatch: pytest.MonkeyPatch, code: int, status: Status
) -> None:
    # Given: the two codes the check could already decide.
    executor = _executor(monkeypatch, code)

    # When: it runs.
    (result,) = executor.execute(Check("discord-readiness"))

    # Then: the wording is byte-identical to before — docs/guide/install.md §8 quotes
    # `[FAIL] discord-readiness: discord_check.py rc=1` verbatim in its symptom table.
    assert result.status is status
    assert result.detail == f"discord_check.py rc={code}"


# --------------------------------------------------------------------------- #
# What the installer hands back to the owner.
# --------------------------------------------------------------------------- #
def test_the_owner_is_given_the_command_that_closes_the_warning() -> None:
    # The import is local because this module is what the change adds; keeping it out of
    # the file header lets the tests above report the real defect instead of a collection
    # error while it does not exist yet.
    from automation.install.owner_actions import follow_up

    # Given: a finished install in which one check could not be decided.
    results = (
        CheckResult("discord-readiness", Status.WARN, "TOKEN-ABSENT: …"),
        CheckResult("healthcheck", Status.PASS, "healthcheck.sh ALL_HEALTHY"),
    )

    # When: the installer renders what is left for the owner.
    rendered = follow_up(results, discord_config=_DISCORD_CONFIG)

    # Then: the exact command is there with this installation's own config path — an
    # operator should not have to reconstruct it from the source.
    assert "discord_check.py" in rendered
    assert str(_DISCORD_CONFIG) in rendered
    assert "healthcheck" not in rendered


def test_the_owner_is_given_nothing_when_the_installer_decided_everything() -> None:
    from automation.install.owner_actions import follow_up

    # Given: a run in which every check reached a verdict.
    results = (CheckResult("healthcheck", Status.PASS, "healthcheck.sh ALL_HEALTHY"),)

    # When / Then: the report is unchanged. A trailing block on a clean install trains
    # the operator to skip it on the run where it matters.
    assert follow_up(results, discord_config=_DISCORD_CONFIG) == ""


def test_the_owner_still_sees_a_warning_nobody_wrote_steps_for() -> None:
    from automation.install.owner_actions import follow_up

    # Given: some future check that warns.
    results = (CheckResult("some-new-check", Status.WARN, "UNDECIDED: 이유"),)

    # When: the block is rendered.
    rendered = follow_up(results, discord_config=_DISCORD_CONFIG)

    # Then: it says so rather than printing a heading over an empty list, which would
    # read as "nothing left to do".
    assert "some-new-check" in rendered
    assert "UNDECIDED: 이유" in rendered


@pytest.mark.parametrize("returncode", (0, 1))
def test_the_wizard_does_not_swallow_what_is_left_for_the_owner(returncode: int) -> None:
    from automation.install.owner_actions import FOLLOW_UP_HEADING, follow_up
    from automation.install.wizard_screens import summarize_verdict

    # Given: an install whose report the wizard captured — the wizard is the documented
    # entry point, so its screen is where an operator actually reads the outcome.
    warned = CheckResult("discord-readiness", Status.WARN, "TOKEN-ABSENT: …")
    output = "\n".join(
        (
            "[PASS] hermes-gateway: agent and peer Hermes gateways are active",
            f"[WARN] {warned.name}: {warned.detail}",
            "[PASS] healthcheck: healthcheck.sh ALL_HEALTHY",
            "--- INSTALLED: 3건 중 실패 0 / 경고 1",
            follow_up((warned,), discord_config=_DISCORD_CONFIG),
        )
    )

    # When: the wizard summarises it.
    summary = summarize_verdict(output, returncode=returncode)

    # Then: the block survives. summarize_verdict keeps an allowlist of lines, and an
    # allowlist written before this block existed drops it — the installer would tell the
    # owner what is left and the wizard would hide it.
    assert FOLLOW_UP_HEADING in summary
    assert "discord_check.py" in summary
