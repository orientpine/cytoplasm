"""Unsigned origin/main observation and first-tick notification wiring."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import automation.deploy_reconcile_cli as reconcile_cli
from automation.deploy_reconcile import (
    BACKLOG_NOTICE_SECONDS,
    Backlog,
    FAILURE_NOTICE_THRESHOLD,
    ReconcileState,
    reconcile_unsigned_head,
)
from automation.deploy_reconcile_unsigned import observe_release_backlog, raw_remote_main_sha
from automation.git_tag_signature import GitRunner
from automation.update_trust import UpdateTrustError

_A = "a" * 40
_B = "b" * 40


def _runner(
    stdout: str,
    observed: list[tuple[list[str], float]] | None = None,
) -> GitRunner:
    def run(
        args: list[str],
        /,
        *,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del env, capture_output, text
        if observed is not None:
            observed.append((args, timeout))
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    return run


def test_raw_remote_main_sha_uses_only_read_only_ls_remote(tmp_path: Path) -> None:
    # Given: the remote reports one exact origin/main object id.
    observed: list[tuple[list[str], float]] = []

    # When: the advisory observer reads the public head.
    result = raw_remote_main_sha(
        tmp_path,
        "git@example.invalid:group/repo.git",
        _runner(f"{_B}\trefs/heads/main\n", observed),
    )

    # Then: the only command is a read-only exact-ref query.
    assert result == _B
    assert observed == [
        (
            [
                "git",
                "-C",
                str(tmp_path),
                "ls-remote",
                "git@example.invalid:group/repo.git",
                "refs/heads/main",
            ],
            30.0,
        )
    ]


@pytest.mark.parametrize(
    "stdout",
    (
        "",
        f"{_B}\trefs/heads/not-main\n",
        "not-an-object-id\trefs/heads/main\n",
        f"{_B}\trefs/heads/main\n{_A}\trefs/heads/main\n",
    ),
)
def test_raw_remote_main_sha_rejects_malformed_results(
    tmp_path: Path,
    stdout: str,
) -> None:
    # Given: the advisory read is absent, wrong-ref, malformed, or ambiguous.
    # When/Then: no SHA is exposed to the notice lifecycle.
    assert raw_remote_main_sha(tmp_path, runner=_runner(stdout)) == ""


@pytest.mark.parametrize("mirror_state", ("dirty", "ahead"))
def test_backlog_digest_explains_a_frozen_observation_mirror(mirror_state: str) -> None:
    notices: list[str] = []
    state = reconcile_unsigned_head(
        ReconcileState(),
        remote_head=_B,
        current_sha=_A,
        now=0.0,
        deliver=lambda notice: not notices.append(notice),
        mirror_state=mirror_state,
    )

    _ = reconcile_unsigned_head(
        state,
        remote_head=_B,
        current_sha=_A,
        now=BACKLOG_NOTICE_SECONDS + 1.0,
        deliver=lambda notice: not notices.append(notice),
        mirror_state=mirror_state,
    )

    assert len(notices) == 1
    assert "관측 미러 `/srv/autophagy-agents`가 미커밋/미푸시 작업으로 동결되어" in notices[0]
    assert "git format-patch" in notices[0]
    assert "git reset --hard" in notices[0]


def test_backlog_digest_says_a_behind_mirror_will_follow_after_release() -> None:
    notices: list[str] = []
    state = reconcile_unsigned_head(
        ReconcileState(),
        remote_head=_B,
        current_sha=_A,
        now=0.0,
        deliver=lambda notice: not notices.append(notice),
        mirror_state="behind",
    )

    _ = reconcile_unsigned_head(
        state,
        remote_head=_B,
        current_sha=_A,
        now=BACKLOG_NOTICE_SECONDS + 1.0,
        deliver=lambda notice: not notices.append(notice),
        mirror_state="behind",
    )

    assert len(notices) == 1
    assert "릴리스 후 origin/main을 따라갑니다." in notices[0]
    assert "미커밋/미푸시 작업으로 동결" not in notices[0]


def test_clean_and_unknown_mirror_states_leave_the_backlog_digest_unchanged() -> None:
    notices: list[str] = []
    for mirror_state in ("clean", "unknown"):
        state = reconcile_unsigned_head(
            ReconcileState(),
            remote_head=_B,
            current_sha=_A,
            now=0.0,
            deliver=lambda notice: not notices.append(notice),
            mirror_state=mirror_state,
        )
        _ = reconcile_unsigned_head(
            state,
            remote_head=_B,
            current_sha=_A,
            now=BACKLOG_NOTICE_SECONDS + 1.0,
            deliver=lambda notice: not notices.append(notice),
            mirror_state=mirror_state,
        )

    assert len(notices) == 2
    assert notices[0] == notices[1]


def test_main_records_the_release_backlog_on_the_success_path_without_paging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """VA-3 백로그는 이제 수렴이 성공한 뒤에 관측된다.

    수렴이 origin/main tip 동일성을 요구하던 동안에는 릴리스 사이 매 틱이 UNSIGNED-HEAD 였고
    시계가 그 예외 위에서 돌았다. 이제 노드는 tip 과 무관하게 최신 릴리스로 수렴하므로, 그
    릴리스 뒤에 쌓인 미배포 커밋이 같은 다이제스트 경로로 들어가야 한다.
    """
    calls: list[str] = []
    notices: list[str] = []

    def unexpected_release(_target: str, _prior: str) -> int:
        calls.append("release")
        return 0

    monkeypatch.setattr(reconcile_cli, "candidate_update_sha", lambda *_a: _A)
    monkeypatch.setattr(
        reconcile_cli, "observe_release_backlog", lambda *_a, **_k: Backlog(_B, 4, "dirty")
    )
    monkeypatch.setattr(reconcile_cli, "roster_update_channel", lambda: None)
    monkeypatch.setattr(reconcile_cli, "unconfigured_reason", lambda _config: None)
    monkeypatch.setattr(reconcile_cli, "DEFAULT_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(reconcile_cli, "run_release_update", unexpected_release)
    monkeypatch.setattr(reconcile_cli, "current_release_sha", lambda: _A)
    monkeypatch.setattr(reconcile_cli, "persist_update_channel_binding", lambda *_a: None)
    monkeypatch.setattr(reconcile_cli, "sync_mirror", lambda *_a, **_k: "in-sync")
    monkeypatch.setattr(reconcile_cli, "notify_owner", lambda notice: not notices.append(notice))

    results = [reconcile_cli.main() for _ in range(FAILURE_NOTICE_THRESHOLD)]

    # 임계 전에는 침묵하고, 이미 그 릴리스에 있으므로 특권 헬퍼도 부르지 않는다.
    assert results == [0] * FAILURE_NOTICE_THRESHOLD
    assert calls == []
    assert notices == []
    state = reconcile_cli.load_state(tmp_path / "state.json")
    assert state.skip_reason == "release-backlog"
    assert state.notified_target is None
    assert state.consecutive_failures == FAILURE_NOTICE_THRESHOLD
    assert state.mirror_state == "dirty"


def test_observe_release_backlog_is_silent_when_the_tip_is_the_release() -> None:
    """tip 이 곧 설치 대상이면 백로그가 없다 — 세지도, 미러를 묻지도 않는다."""
    asked: list[str] = []

    observed = observe_release_backlog(
        Path("/mirror"),
        _A,
        update_channel=None,
        mirror_state=lambda _channel: asked.append("asked") or "dirty",
        runner=_runner(f'{_A}\trefs/heads/main\n'),
    )

    assert observed == Backlog()
    assert asked == []


def test_observe_release_backlog_measures_the_gap_when_the_tip_is_ahead() -> None:
    """tip 이 앞서면 그 격차를 재고 미러 상태를 함께 싣는다."""
    observed = observe_release_backlog(
        Path("/mirror"),
        _A,
        update_channel=None,
        mirror_state=lambda _channel: "dirty",
        runner=_runner(f'{_B}\trefs/heads/main\n'),
    )

    assert observed.head == _B
    assert observed.mirror_state == "dirty"

def test_unsigned_head_with_unresolved_sha_keeps_threshold_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: signature resolution identifies UNSIGNED-HEAD but advisory re-read fails.
    notices: list[str] = []

    def blocked_target() -> str:
        raise UpdateTrustError("UNSIGNED-HEAD", "origin/main lacks a signed release tag")

    monkeypatch.setattr(reconcile_cli, "candidate_update_sha", blocked_target)
    monkeypatch.setattr(reconcile_cli, "roster_update_channel", lambda: None)
    monkeypatch.setattr(reconcile_cli, "unconfigured_reason", lambda _config: None)
    monkeypatch.setattr(reconcile_cli, "DEFAULT_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(reconcile_cli, "notify_owner", lambda notice: not notices.append(notice))

    # When: the generic threshold elapses.
    results = [reconcile_cli.main() for _ in range(FAILURE_NOTICE_THRESHOLD)]

    # Then: there is no immediate unresolved message; the legacy fallback fires once.
    assert results == [0] * FAILURE_NOTICE_THRESHOLD
    assert len(notices) == 1
    assert "unresolved" in notices[0]
    assert reconcile_cli.load_state(tmp_path / "state.json").notified_target == (
        "skip:update-trust-block"
    )


def test_non_unsigned_trust_error_keeps_threshold_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: the trust boundary failed for a reason other than a missing release tag.
    notices: list[str] = []

    def blocked_target() -> str:
        raise UpdateTrustError("TAG-FETCH", "remote unavailable")

    monkeypatch.setattr(reconcile_cli, "candidate_update_sha", blocked_target)
    monkeypatch.setattr(reconcile_cli, "roster_update_channel", lambda: None)
    monkeypatch.setattr(reconcile_cli, "unconfigured_reason", lambda _config: None)
    monkeypatch.setattr(reconcile_cli, "DEFAULT_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(reconcile_cli, "notify_owner", lambda notice: not notices.append(notice))

    # When: three identical trust failures occur.
    results = [reconcile_cli.main() for _ in range(FAILURE_NOTICE_THRESHOLD)]

    # Then: the legacy threshold path remains for a transport-level trust failure.
    assert results == [0] * FAILURE_NOTICE_THRESHOLD
    assert len(notices) == 1
    assert reconcile_cli.load_state(tmp_path / "state.json").notified_target == (
        "skip:update-trust-block"
    )
