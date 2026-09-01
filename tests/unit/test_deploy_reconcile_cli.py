"""Wiring for the reconciler tick, and the two systemd settings that would silently break it.

MD-2. The decision core is pure; this pins the edges that only exist on the node, where
being wrong is expensive and invisible:

* ``PrivateTmp=yes`` would give the timer its OWN ``/tmp``. That used to split the
  shared convergence lock in two (timer and ``land.sh`` installing out of order,
  flipping the runtime backwards). The lock has since moved under
  ``/srv/autophagy-private`` — ``/tmp`` could not host it at all, because
  ``fs.protected_regular=2`` refuses a cross-owner open in a sticky world-writable
  directory and root could never open the lock ops had created (2026-08-01). The
  setting is still pinned: it keeps this timer's temp behaviour identical to the
  ops-side converger, one less difference between two paths that must agree.
* ``NoNewPrivileges=yes`` — which the neighbouring repair unit sets, and which is the
  obvious thing to copy — makes ``sudo`` impossible. The reconciler's whole job is to
  call the privileged helper through ``sudo -n``, so copying that line would produce a
  timer that runs every two minutes and can never converge anything.

The notifier is deliberately a seam. Whether the existing Ops bot DM path can carry
incident notices is unmeasured, and guessing would be worse than queueing: an
unconfigured notifier reports failure, the notice is retained in state, and nothing is
lost — the tick still converges, which is the part that keeps prod correct.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import automation.deploy_reconcile_cli as reconcile_cli
from automation.deploy_reconcile import FAILURE_NOTICE_THRESHOLD
from automation.deploy_reconcile_cli import (
    converge_command,
    current_release_sha,
)
from automation.node_asset_renderer import render_asset
from automation.node_config import default_node_config
from automation.owner_notice import notify_owner

_REPO = Path(__file__).resolve().parents[2]
_SERVICE = _REPO / "automation" / "systemd" / "autophagy-deploy-reconcile.service"


def _service_text() -> str:
    return render_asset(_SERVICE, default_node_config())
_TIMER = _REPO / "automation" / "systemd" / "autophagy-deploy-reconcile.timer"

_HELPER = "/usr/local/libexec/autophagy-converge-origin-main"


def test_private_tmp_is_never_enabled() -> None:
    """A private /tmp would silently split the convergence lock in two."""
    text = _service_text()
    assert "PrivateTmp=yes" not in text
    assert "PrivateTmp=no" in text, "state it explicitly so nobody 'hardens' it later"


def test_no_new_privileges_is_not_set_because_the_tick_must_sudo() -> None:
    text = _service_text()
    assert "NoNewPrivileges=yes" not in text


def test_service_runs_as_ops_with_home_protected() -> None:
    text = _service_text()
    assert "User=ops" in text and "Group=ops" in text
    # tmpfs 도 /home · /root · /run/user 를 빈 읽기전용 파일시스템으로 덮는다 —
    # 감춰지는 것은 `yes` 와 같고, 아래 테스트가 왜 그것이어야 하는지를 고정한다.
    assert "ProtectHome=tmpfs" in text


def test_protected_home_still_lets_the_tick_reach_origin() -> None:
    """`ProtectHome=yes` 단독은 이 타이머를 **조용히** 무력화한다.

    origin 은 SSH 원격(`git@github.com:...`)이고 ssh 는 `~/.ssh` 를 환경변수가 아니라
    passwd 항목으로 찾는다. ProtectHome 은 그 `/home` 을 서비스에게 빈 디렉터리로
    보여주므로 키가 사라지고, `ls-remote` 가 실패하면 tick 은 '원격 장애는 드리프트가
    아니다'라며 **exit 0 으로 정상 종료**한다. 실패 카운터도 오르지 않고 알림도 없다.

    실측(2026-08-02): 타이머가 15시간·약 450회 돌았는데 상태 파일은 그 전날 수동
    실행 이후로 한 번도 갱신되지 않았다 — 매번 save_state 에 닿기 전에 빠졌다는 뜻.

    그래서 `.ssh` **한 디렉터리만** 읽기 전용으로 되노출한다. 키를 복사하지
    않으므로 사본이 드리프트할 여지도 없다.

    그리고 그 되노출은 **`tmpfs` 일 때만 가능하다**. systemd.exec(5) 가 명시한다 —
    "it is not possible to use those options for mount points nested underneath ...
    /home/ ... if ProtectHome=yes is specified. ... ProtectHome=tmpfs should be used
    instead." (노드의 systemd 255 man 페이지에서 직접 확인)

    즉 `yes` 로 되돌리는 것은 하드닝이 아니다 — 감춰지는 범위는 똑같고(둘 다 빈
    /home), 대신 프로덕션이 영원히 수렴하지 않게 된다."""
    # 지시어 줄만 본다 — 이 유닛의 주석은 "`yes` 를 쓰지 말라"를 설명하면서
    # `ProtectHome=yes` 를 인용하므로, 파일 전체 문자열 검색은 그것에 걸린다.
    directives = [
        line.strip()
            for line in _service_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert "ProtectHome=tmpfs" in directives, "bind 되노출은 tmpfs 에서만 동작한다"
    assert "ProtectHome=yes" not in directives, "`yes` 는 조용히 수렴을 멈춘다"
    text = "\n".join(directives)
    assert "BindReadOnlyPaths=/home/ops/.ssh" in text
    assert "BindReadOnlyPaths=-/home/ops/.hermes/node.toml" in text
    # 쓰기 가능하게 되노출하면 타이머가 자격증명을 변경할 수 있게 된다.
    assert "BindPaths=/home/ops/.ssh" not in text


def test_protected_home_still_lets_the_tick_restart_the_gateway_pair() -> None:
    """`ProtectHome=` 는 `/run/user` 도 덮는다 — 그곳이 제어 소켓이 사는 곳이다.

    `autophagy-gateway-pair` 는 각 Hermes 게이트웨이를
    `runuser -u <account> -- env XDG_RUNTIME_DIR=/run/user/<uid> systemctl --user restart`
    로 재시작한다. systemd.exec(5) 가 명시한다 — "the directories /home/, /root,
    and /run/user are made inaccessible and empty"(노드의 systemd 255 man 페이지에서
    직접 확인). 그러면 `$XDG_RUNTIME_DIR/systemd/private` 가 아예 없어 클라이언트가
    1초도 안 돼서 죽는다.

    실측 2026-08-16·2026-08-19: 두 번의 수렴이 모두 릴리스를 설치한 **뒤** 이
    단계에서 `reason=gateway-restart` 로 롤백됐고, 그 사이 프로덕션은 3일간
    얼어 있었다. 같은 헬퍼를 로그인 세션(=샌드박스 밖)에서 돌리면
    `active`/`active` rc=0 이다 — 즉 변수는 헬퍼가 아니라 이 유닛의 마운트
    네임스페이스 하나뿐이다.

    되노출은 read-write 여야 한다: unix 소켓 connect 는 파일시스템 읽기가 아니다.
    그리고 계정별 `/run/user/<uid>` 는 여전히 0700 이므로, 이것은 실제로 재시작을
    수행하는 root 헬퍼가 이미 닿을 수 있던 것 외에 아무것도 더 열어주지 않는다."""
    directives = [
        line.strip()
        for line in _service_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert "BindPaths=/run/user" in directives, (
        "ProtectHome 이 /run/user 를 비우면 게이트웨이 재시작이 매번 실패해 "
        "모든 수렴이 빌드 직후 롤백된다"
    )
    assert "BindReadOnlyPaths=/run/user" not in directives, (
        "소켓 connect 는 read-only 되노출로는 성립하지 않는다"
    )


def test_service_may_write_its_state_directory_and_the_mirror_it_carries() -> None:
    """Two paths, each earned. The mirror is here because the tick fast-forwards it."""
    text = _service_text()
    assert "ReadWritePaths=/srv/autophagy-private/deploy-reconcile" in text
    directives = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    writable = next(d for d in directives if d.startswith("ReadWritePaths="))
    assert "/srv/autophagy-agents" in writable, (
        "sync_mirror ff-pulls the observation checkout; a later ProtectSystem=strict "
        "would otherwise stop it silently"
    )


def test_service_invokes_the_reconcile_cli() -> None:
    assert "automation/deploy_reconcile_cli.py" in _service_text()


def test_timer_ticks_every_two_minutes_and_catches_up_after_downtime() -> None:
    text = _TIMER.read_text(encoding="utf-8")
    assert "OnUnitActiveSec=2min" in text
    assert "Persistent=true" in text


def test_converge_command_is_the_fixed_helper_with_no_arguments() -> None:
    """Anything appended here would become an injection seam into a root helper."""
    assert converge_command() == ("sudo", "-n", _HELPER)


def test_current_release_sha_reads_the_live_release_pointer(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    sha = "a" * 40
    (releases / sha).mkdir(parents=True)
    pointer = tmp_path / "current"
    pointer.symlink_to(releases / sha, target_is_directory=True)
    assert current_release_sha(pointer) == sha


def test_current_release_sha_is_empty_when_the_pointer_is_absent_or_broken(
    tmp_path: Path,
) -> None:
    """Absent or dangling reads as 'not converged', never as 'converged to nothing'."""
    assert current_release_sha(tmp_path / "missing") == ""
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "gone", target_is_directory=True)
    assert current_release_sha(dangling) == ""


def test_main_when_update_target_stays_unresolved_then_notifies_at_failure_threshold(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    notices: list[str] = []
    monkeypatch.setattr(reconcile_cli, "candidate_update_sha", lambda: "")
    monkeypatch.setattr(reconcile_cli, "roster_update_channel", lambda: None)
    monkeypatch.setattr(reconcile_cli, "unconfigured_reason", lambda _config: None)
    monkeypatch.setattr(reconcile_cli, "DEFAULT_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(reconcile_cli, "notify_owner", lambda notice: not notices.append(notice))

    results = [reconcile_cli.main() for _ in range(FAILURE_NOTICE_THRESHOLD)]

    assert results == [0] * FAILURE_NOTICE_THRESHOLD
    assert len(notices) == 1
    assert reconcile_cli.load_state(tmp_path / "state.json").consecutive_failures == (
        FAILURE_NOTICE_THRESHOLD
    )


def test_main_when_channel_binding_stays_blocked_then_notifies_at_failure_threshold(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    notices: list[str] = []
    monkeypatch.setattr(reconcile_cli, "candidate_update_sha", lambda: "b" * 40)
    monkeypatch.setattr(reconcile_cli, "roster_update_channel", lambda: None)
    monkeypatch.setattr(reconcile_cli, "unconfigured_reason", lambda _config: None)
    monkeypatch.setattr(
        reconcile_cli,
        "persist_update_channel_binding",
        lambda _channel, _path: (_ for _ in ()).throw(OSError("read-only")),
    )
    monkeypatch.setattr(reconcile_cli, "DEFAULT_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(reconcile_cli, "notify_owner", lambda notice: not notices.append(notice))

    results = [reconcile_cli.main() for _ in range(FAILURE_NOTICE_THRESHOLD)]

    assert results == [0] * FAILURE_NOTICE_THRESHOLD
    assert len(notices) == 1
    assert reconcile_cli.load_state(tmp_path / "state.json").consecutive_failures == (
        FAILURE_NOTICE_THRESHOLD
    )


def test_main_when_signed_update_is_trusted_then_reconciles_to_tag_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: signature verification resolved the public release tag to one commit.
    target = "b" * 40
    observed_targets: list[str] = []
    observed_transitions: list[tuple[str, str]] = []

    def candidate() -> str:
        return target

    def current() -> str:
        return "a" * 40

    def transition(candidate_sha: str, prior_sha: str) -> int:
        observed_transitions.append((candidate_sha, prior_sha))
        return 0

    def deliver(_notice: str) -> bool:
        return True

    def sync(candidate_sha: str) -> str:
        observed_targets.append(candidate_sha)
        return reconcile_cli.MIRROR_IN_SYNC

    monkeypatch.setattr(reconcile_cli, "candidate_update_sha", candidate)
    monkeypatch.setattr(reconcile_cli, "roster_update_channel", lambda: None)
    monkeypatch.setattr(reconcile_cli, "unconfigured_reason", lambda _config: None)
    monkeypatch.setattr(reconcile_cli, "current_release_sha", current)
    monkeypatch.setattr(reconcile_cli, "DEFAULT_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(reconcile_cli, "UPDATE_CHANNEL_STATE", tmp_path / "update-channel.json")
    monkeypatch.setattr(reconcile_cli, "run_release_update", transition)
    monkeypatch.setattr(reconcile_cli, "notify_owner", deliver)
    monkeypatch.setattr(reconcile_cli, "sync_mirror", sync)

    # When: the reconciliation timer runs.
    result = reconcile_cli.main()

    # Then: all downstream decisions use the verified tag commit, never a fresh main lookup.
    assert result == 0
    assert observed_transitions == [(target, "a" * 40)]
    assert observed_targets == [target]
    assert (tmp_path / "state.json").is_file()


def test_the_notifier_is_wired_and_still_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """자리표시자는 사라졌지만 그 계약은 남는다 — 전달 안 되면 False, 그리고 시끄럽게.

    조용히 큐잉된 통지는 이 기능이 없애려던 실패 양식 그 자체다.
    실제 전송 경로의 상세 계약은 tests/unit/test_deploy_reconcile_notifier.py 가 고정한다."""
    # monkeypatch 로 지운다 — os.environ 을 직접 건드리면 다른 테스트로 샐다.
    for name in ("DISCORD_BOT_TOKEN", "AUTOPHAGY_OWNER_ID"):
        monkeypatch.delenv(name, raising=False)
    assert notify_owner("prod is behind") is False
    assert "NOTIFY" in capsys.readouterr().err
