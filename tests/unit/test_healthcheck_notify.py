"""healthcheck 실패가 소유자에게 닿되, 쏟아지지는 않게.

2026-08-02 실측이 이 파일의 이유다. 노드 에이전트가 배포 체크아웃에 커밋해 9시간 동안 모든
ff-pull 이 막혔을 때 healthcheck 는 그것을 **52번 FAIL 로 정확히 탐지하고도** 소유자에게
닿지 못했다. 탐지는 있는데 도달이 없었다.

그런데 도달만 붙이면 반대쪽으로 넘어간다 — 5분마다 도는 스윕이 같은 사건으로 52통을 보내면
그 알림은 곧 무시되고, 무시되는 알림은 없는 알림과 같다. 그래서 이 모듈이 고정하는 것은
**"보낸다"가 아니라 "사건당 한 번만 보낸다"**이다.

집계 단위는 **스윕 1회당 메시지 1통**이다. 새로 실패한 체크와 새로 회복한 체크를 한 통에
담고, 새로운 것이 없으면 아무것도 보내지 않는다. SSH 전면 장애로 9개가 한꺼번에 무너져도
9통이 아니라 1통이다.

순수 함수로 둔다 — 전송·시각·상태 저장은 호출자가 넘긴다. 그래야 "정확히 한 통"이 희망이
아니라 단위 테스트가 된다.
"""
from __future__ import annotations

from automation.healthcheck_notify import NotifyState, plan_notice


def test_a_healthy_sweep_says_nothing() -> None:
    state, notice = plan_notice(NotifyState(), failing=())
    assert notice is None
    assert state.open_incidents == ()


def test_a_new_failure_is_reported_once() -> None:
    # Given: 처음 실패한 체크
    state, notice = plan_notice(NotifyState(), failing=("example-primary-node model gateway",))
    assert notice is not None
    assert "example-primary-node model gateway" in notice
    assert state.open_incidents == ("example-primary-node model gateway",)


def test_the_same_failure_repeating_says_nothing_more() -> None:
    """9시간 52회가 52통이 되면 그 알림은 곧 무시된다."""
    state, _ = plan_notice(NotifyState(), failing=("example-primary-node model gateway",))
    for _ in range(50):
        state, notice = plan_notice(state, failing=("example-primary-node model gateway",))
        assert notice is None
    assert state.open_incidents == ("example-primary-node model gateway",)


def test_many_checks_failing_at_once_are_one_message() -> None:
    """SSH 전면 장애로 9개가 무너져도 9통이 아니라 1통이다."""
    failing = tuple(f"check-{index}" for index in range(9))
    state, notice = plan_notice(NotifyState(), failing=failing)
    assert notice is not None
    assert notice.count("check-") == 9
    assert state.open_incidents == failing


def test_recovery_is_reported_once_and_closes_the_incident() -> None:
    state, _ = plan_notice(NotifyState(), failing=("db",))
    state, notice = plan_notice(state, failing=())
    assert notice is not None and "db" in notice
    assert state.open_incidents == ()
    # 그리고 다시 조용해진다
    state, quiet = plan_notice(state, failing=())
    assert quiet is None


def test_a_new_failure_beside_an_open_one_reports_only_the_new() -> None:
    state, _ = plan_notice(NotifyState(), failing=("db",))
    state, notice = plan_notice(state, failing=("db", "cache"))
    assert notice is not None
    assert "cache" in notice
    assert "db" not in notice, "이미 알린 사건을 다시 알리면 집계가 무의미해진다"
    assert set(state.open_incidents) == {"db", "cache"}


def test_a_failure_and_a_recovery_in_one_sweep_share_one_message() -> None:
    state, _ = plan_notice(NotifyState(), failing=("db",))
    state, notice = plan_notice(state, failing=("cache",))
    assert notice is not None
    assert "cache" in notice and "db" in notice
    assert state.open_incidents == ("cache",)


def test_open_incidents_stay_sorted_so_the_state_is_comparable() -> None:
    state, _ = plan_notice(NotifyState(), failing=("z", "a", "m"))
    assert state.open_incidents == ("a", "m", "z")


def test_the_notice_names_the_failing_check() -> None:
    """본문에 체크 이름이 그대로 있어야 조사를 바로 시작할 수 있다."""
    _, notice = plan_notice(NotifyState(), failing=("example-primary-node report-hub dashboard",))
    assert notice is not None
    assert "example-primary-node report-hub dashboard" in notice



def test_check_names_takes_the_name_out_of_a_sweep_definition() -> None:
    """healthcheck.sh 는 `<이름>|<타입>|<노드>|<계정>|<대상>` 을 그대로 넘긴다.

    bash 에서 이름을 뽑으려면 스윈 루프 안에 한 줄이 더 필요한데, healthcheck.sh 는
    250 pure-LOC 게이트에 이미 닿아 있어 배선이 정확히 한 줄이어야 한다."""
    from automation.healthcheck_notify import check_names

    assert check_names(
        ("example-primary-node model gateway|http_200|example-primary-node|ops|http://127.0.0.1:4000/health",)
    ) == ("example-primary-node model gateway",)


def test_check_names_keeps_a_bare_name_and_drops_empties() -> None:
    from automation.healthcheck_notify import check_names

    assert check_names(("plain", "", "a|b")) == ("plain", "a")
