"""릴리스가 **실제로 적용되었을 때만** 소유자 DM 이 한 번 나가는 것을 고정한다.

승인 반응(✅)은 "적용"이 아니다. 적용의 근거는 셋을 모두 통과한 경우뿐이다 —
완결기의 성공 마커(`completed/<sha>`) · 노드의 활성 릴리스 포인터 · 전량 재판정
영수증(`deploy-all/receipt.json`). 하나라도 어긋나면 보내지 않고 다음 틱에 다시 본다.

번호는 **추정하지 않는다**: 그 sha 에 달린 릴리스 태그 하나가 유일한 출처이고, 태그가
없으면 보류(다음 틱), 둘이면 영구 보류다(사람이 정리해야 한다).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from automation import owner_notice, release_applied_notice
from automation.release_applied_notice import Applied, ReleaseFacts, Retry, Skip

_SHA = "29a0a59087ca4e35b1d00cf8a3254bfc4d85e22a"
_OTHER = "a17b051f49dd3270f70cf0b04798902c1784f719"

#: 2026-09-05 노드 실측 형태 — `readlink <current>` 한 줄 뒤에 영수증 JSON 이 이어진다.
_REAL_PROBE_SAMPLE = f"""/srv/autophagy-agent-releases/{_SHA}
{{
  "delegated": [
    "release-helpers",
    "runtime-packages",
    "rag-stack"
  ],
  "release_sha": "{_SHA}",
  "surfaces": {{
    "home_artifacts": {{
      "ok": 29,
      "ok_absent_optional": 1
    }},
    "skill_mounts": "ok"
  }},
  "undeclared": [
    {{
      "account": "agent",
      "destination": ".hermes/plugins/interop-protocol/plugin.yaml",
      "sha256_prefix": "c0d741019066"
    }}
  ],
  "verified_at": "2026-09-05T10:49:15+00:00",
  "version": 1
}}
"""

_PROBE_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$PROBE_CALLS"
printf '%s' "$PROBE_OUT"
exit "${PROBE_RC:-0}"
"""

_SEND_STUB = """#!/usr/bin/env bash
printf '%s|%s\\n' "${RELEASE_APPROVAL_MODULE:-unset}" "$*" >> "$SEND_CALLS"
exit "${SEND_RC:-0}"
"""


def _facts(
    *,
    version_tags: tuple[str, ...] = ("v1.2.4",),
    pointer_sha: str | None = _SHA,
    receipt_sha: str | None = _SHA,
    superseded: bool = False,
) -> ReleaseFacts:
    return ReleaseFacts(
        sha=_SHA,
        version_tags=version_tags,
        pointer_sha=pointer_sha,
        receipt_sha=receipt_sha,
        superseded=superseded,
    )


class TestDecision:
    """판정은 순수 함수다 — 노드도 네트워크도 보지 않고 사실만 본다."""

    def test_a_all_three_signals_agree_is_applied(self) -> None:
        assert release_applied_notice.decide(_facts()) == Applied("v1.2.4")

    def test_b_superseded_release_is_never_notified(self) -> None:
        # Given: 이 sha 의 완결 마커는 있지만 노드는 이미 그 뒤 릴리스를 돌린다
        decision = release_applied_notice.decide(
            _facts(pointer_sha=_OTHER, receipt_sha=_OTHER, superseded=True)
        )

        # Then: 지나간 릴리스는 "적용됨"이 아니다 — 영구 보류
        assert decision == Skip("SUPERSEDED")

    def test_c_missing_tag_waits_instead_of_guessing(self) -> None:
        assert release_applied_notice.decide(_facts(version_tags=())) == Retry("NO-TAG")

    def test_d_two_tags_is_permanent_because_the_number_cannot_be_guessed(self) -> None:
        decision = release_applied_notice.decide(_facts(version_tags=("v1.1.5", "v1.2.0")))

        assert decision == Skip("VERSION-AMBIGUOUS")

    def test_e_unreadable_pointer_waits(self) -> None:
        assert release_applied_notice.decide(_facts(pointer_sha=None)) == Retry(
            "POINTER-UNREADABLE"
        )

    def test_f_pointer_on_another_release_waits(self) -> None:
        assert release_applied_notice.decide(_facts(pointer_sha=_OTHER)) == Retry(
            "POINTER-BEHIND"
        )

    def test_g_missing_receipt_waits(self) -> None:
        assert release_applied_notice.decide(_facts(receipt_sha=None)) == Retry(
            "RECEIPT-MISSING"
        )

    def test_h_stale_receipt_waits(self) -> None:
        assert release_applied_notice.decide(_facts(receipt_sha=_OTHER)) == Retry(
            "RECEIPT-STALE"
        )

    def test_i_superseded_outranks_a_missing_tag(self) -> None:
        """이미 지나간 릴리스는 태그가 없어도 매 틱 재시도로 남지 않는다."""
        decision = release_applied_notice.decide(_facts(version_tags=(), superseded=True))

        assert decision == Skip("SUPERSEDED")


class TestProbeParsing:
    def test_a_real_node_sample_yields_pointer_and_receipt(self) -> None:
        assert release_applied_notice.parse_probe(_REAL_PROBE_SAMPLE) == (_SHA, _SHA)

    def test_b_pointer_only_means_the_receipt_is_missing(self) -> None:
        sample = f"/srv/autophagy-agent-releases/{_SHA}\n"

        assert release_applied_notice.parse_probe(sample) == (_SHA, None)

    def test_c_receipt_only_means_the_pointer_is_unreadable(self) -> None:
        sample = '{"release_sha": "%s"}\n' % _SHA

        assert release_applied_notice.parse_probe(sample) == (None, _SHA)

    def test_d_truncated_json_is_not_a_receipt(self) -> None:
        sample = f"/srv/autophagy-agent-releases/{_SHA}\n{{\"release_sha\": \"{_SHA}\""

        assert release_applied_notice.parse_probe(sample) == (_SHA, None)

    def test_e_receipt_without_release_sha_is_not_a_receipt(self) -> None:
        sample = f'/srv/autophagy-agent-releases/{_SHA}\n{{"version": 1}}\n'

        assert release_applied_notice.parse_probe(sample) == (_SHA, None)

    def test_f_empty_output_is_fail_closed(self) -> None:
        assert release_applied_notice.parse_probe("") == (None, None)


def _git(cwd: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(cwd), *arguments),
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """두 릴리스 커밋: 옛 것(v1.2.3)과 현재 것(v1.2.4, prerelease 태그가 함께 달림)."""
    root = tmp_path / "repo"
    root.mkdir()
    _ = _git(root, "init", "--quiet", "-b", "main")
    _ = _git(root, "config", "commit.gpgsign", "false")
    _ = (root / "one").write_text("one\n", encoding="utf-8")
    _ = _git(root, "add", "one")
    _ = _git(root, "commit", "--quiet", "-m", "one")
    _ = _git(root, "tag", "v1.2.3")
    _ = (root / "two").write_text("two\n", encoding="utf-8")
    _ = _git(root, "add", "two")
    _ = _git(root, "commit", "--quiet", "-m", "two")
    _ = _git(root, "tag", "v1.2.4")
    _ = _git(root, "tag", "v1.2.4-rc1")
    return root


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


def _previous(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD~1")


class TestReleaseTags:
    def test_a_prerelease_tags_are_not_release_numbers(self, repo: Path) -> None:
        assert release_applied_notice.release_tags(repo, _head(repo)) == ("v1.2.4",)

    def test_b_a_commit_with_two_release_tags_reports_both(self, repo: Path) -> None:
        _ = _git(repo, "tag", "v1.3.0")

        assert release_applied_notice.release_tags(repo, _head(repo)) == ("v1.2.4", "v1.3.0")

    def test_c_untagged_commit_has_no_version(self, repo: Path) -> None:
        _ = (repo / "three").write_text("three\n", encoding="utf-8")
        _ = _git(repo, "add", "three")
        _ = _git(repo, "commit", "--quiet", "-m", "three")

        assert release_applied_notice.release_tags(repo, _head(repo)) == ()


def _stub(path: Path, body: str) -> Path:
    _ = path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def sweep_env(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """스윕이 노드에도 디스코드에도 닿지 않도록 두 이음매를 스텁으로 갈아 끼운다."""
    state = tmp_path / "state"
    (state / "completed").mkdir(parents=True)
    probe = _stub(tmp_path / "probe-stub", _PROBE_STUB)
    send = _stub(tmp_path / "send-stub", _SEND_STUB)
    monkeypatch.setenv("RELEASE_APPLIED_PROBE_CMD", f"bash {probe}")
    monkeypatch.setenv("RELEASE_APPROVAL_CMD", f"bash {send}")
    monkeypatch.setenv("PROBE_CALLS", str(tmp_path / "probe-calls.log"))
    monkeypatch.setenv("SEND_CALLS", str(tmp_path / "send-calls.log"))
    monkeypatch.setenv(
        "PROBE_OUT",
        f"/srv/autophagy-agent-releases/{_head(repo)}\n"
        f'{{"release_sha": "{_head(repo)}"}}\n',
    )
    return state


def _complete(state: Path, sha: str) -> None:
    _ = (state / "completed" / sha).write_text("2026-09-05T00:00:00Z\n", encoding="utf-8")


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _sweep(state: Path, repo: Path) -> int:
    return release_applied_notice.main(["sweep", "--state", str(state), "--repo", str(repo)])


class TestSweep:
    def test_a_applied_release_is_notified_once_with_the_tag_version(
        self, sweep_env: Path, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Given: 완결 마커가 있고 노드 포인터·영수증이 그 sha 를 가리킨다
        head = _head(repo)
        _complete(sweep_env, head)

        # When
        assert _sweep(sweep_env, repo) == 0

        # Then: 태그에서 읽은 번호로 정확히 한 번 보낸다
        output = capsys.readouterr().out
        assert f"RELEASE-APPLIED-NOTIFIED v1.2.4 {head[:12]}" in output
        assert _lines(Path(os.environ["SEND_CALLS"])) == [
            f"automation.release_applied_notice|send --version v1.2.4 --head {head}"
        ]
        marker = sweep_env / "notified" / head
        assert marker.read_text(encoding="utf-8").startswith("v1.2.4 ")
        assert marker.stat().st_mode & 0o777 == 0o600

    def test_b_second_tick_neither_sends_nor_logs(
        self, sweep_env: Path, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Given: 이미 통지한 릴리스
        head = _head(repo)
        _complete(sweep_env, head)
        assert _sweep(sweep_env, repo) == 0
        _ = capsys.readouterr()

        # When: 2분 뒤 다음 틱
        assert _sweep(sweep_env, repo) == 0

        # Then: 멱등 — 발신도 로그도 없다
        assert capsys.readouterr().out == ""
        assert len(_lines(Path(os.environ["SEND_CALLS"]))) == 1

    def test_c_superseded_release_is_recorded_and_never_sent(
        self, sweep_env: Path, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Given: 옛 릴리스의 완결 마커(노드는 이미 다음 릴리스를 돌린다)
        old = _previous(repo)
        _complete(sweep_env, old)

        # When
        assert _sweep(sweep_env, repo) == 0

        # Then: 조용히, 영구적으로 건너뛴다
        assert f"RELEASE-APPLIED-SKIP SUPERSEDED {old[:12]}" in capsys.readouterr().out
        assert (sweep_env / "notify-skipped" / old).read_text(encoding="utf-8").strip() == (
            "SUPERSEDED"
        )
        assert _lines(Path(os.environ["SEND_CALLS"])) == []

    def test_d_ambiguous_version_is_a_permanent_skip(
        self, sweep_env: Path, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Given: 한 커밋에 릴리스 태그가 둘(실측: f00f334ca 의 v1.1.5+v1.2.0)
        head = _head(repo)
        _ = _git(repo, "tag", "v1.3.0")
        _complete(sweep_env, head)

        # When
        assert _sweep(sweep_env, repo) == 0

        # Then: 추정하지 않고 멈춘다
        assert f"RELEASE-APPLIED-SKIP VERSION-AMBIGUOUS {head[:12]}" in capsys.readouterr().out
        assert (sweep_env / "notify-skipped" / head).exists()
        assert _lines(Path(os.environ["SEND_CALLS"])) == []

    def test_e_pointer_behind_waits_without_a_marker(
        self,
        sweep_env: Path,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Given: 완결은 됐지만 노드 포인터가 아직 옛 릴리스다
        head = _head(repo)
        _complete(sweep_env, head)
        monkeypatch.setenv(
            "PROBE_OUT",
            f"/srv/autophagy-agent-releases/{_OTHER}\n" f'{{"release_sha": "{_OTHER}"}}\n',
        )

        # When
        assert _sweep(sweep_env, repo) == 0

        # Then: 마커 없이 다음 틱을 기다린다
        assert f"RELEASE-APPLIED-RETRY POINTER-BEHIND {head[:12]}" in capsys.readouterr().out
        assert not (sweep_env / "notified" / head).exists()
        assert not (sweep_env / "notify-skipped" / head).exists()

    def test_f_stale_receipt_waits(
        self,
        sweep_env: Path,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Given: 포인터는 옮겨졌지만 전량 재판정 영수증이 아직 옛 릴리스다
        head = _head(repo)
        _complete(sweep_env, head)
        monkeypatch.setenv(
            "PROBE_OUT",
            f"/srv/autophagy-agent-releases/{head}\n" f'{{"release_sha": "{_OTHER}"}}\n',
        )

        # When
        assert _sweep(sweep_env, repo) == 0

        # Then
        assert f"RELEASE-APPLIED-RETRY RECEIPT-STALE {head[:12]}" in capsys.readouterr().out
        assert not (sweep_env / "notified" / head).exists()

    def test_g_send_failure_leaves_no_marker_and_retries_next_tick(
        self,
        sweep_env: Path,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Given: 전송이 실패하는 틱
        head = _head(repo)
        _complete(sweep_env, head)
        monkeypatch.setenv("SEND_RC", "9")

        # When
        assert _sweep(sweep_env, repo) == 0

        # Then: 마커를 남기지 않는다 — 재시도는 다음 틱이다
        assert f"RELEASE-APPLIED-SEND-FAIL rc=9 {head[:12]}" in capsys.readouterr().out
        assert not (sweep_env / "notified" / head).exists()

        monkeypatch.setenv("SEND_RC", "0")
        assert _sweep(sweep_env, repo) == 0

        assert (sweep_env / "notified" / head).exists()
        assert len(_lines(Path(os.environ["SEND_CALLS"]))) == 2

    def test_h_no_completed_release_means_no_node_probe(
        self, sweep_env: Path, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Given: 완결된 릴리스가 없는 평범한 틱
        # When
        assert _sweep(sweep_env, repo) == 0

        # Then: 노드를 찌르지 않고 아무 말도 하지 않는다
        assert capsys.readouterr().out == ""
        assert _lines(Path(os.environ["PROBE_CALLS"])) == []

    def test_i_probe_failure_is_a_transient_wait(
        self,
        sweep_env: Path,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Given: ssh 가 실패하는 틱
        head = _head(repo)
        _complete(sweep_env, head)
        monkeypatch.setenv("PROBE_RC", "255")
        monkeypatch.setenv("PROBE_OUT", "")

        # When
        assert _sweep(sweep_env, repo) == 0

        # Then
        assert (
            f"RELEASE-APPLIED-RETRY POINTER-UNREADABLE {head[:12]}" in capsys.readouterr().out
        )
        assert not (sweep_env / "notified" / head).exists()

    def test_j_a_broken_sweep_never_breaks_the_completer(
        self,
        sweep_env: Path,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Given: 프로브 명령 자체가 없는 설치
        _complete(sweep_env, _head(repo))
        monkeypatch.setenv("RELEASE_APPLIED_PROBE_CMD", "/nonexistent/probe")

        # When
        exit_code = _sweep(sweep_env, repo)

        # Then: 완결기는 통지 때문에 죽지 않는다
        assert exit_code == 0
        assert "RELEASE-APPLIED-SWEEP-FAIL FileNotFoundError" in capsys.readouterr().out


class TestSendAtTheNode:
    """노드에서 도는 절반 — 스테이징된 트리에서 agent 자격으로 실행된다."""

    def _pointer(self, tmp_path: Path, target_sha: str) -> Path:
        store = tmp_path / "releases" / target_sha
        store.mkdir(parents=True)
        pointer = tmp_path / "current"
        pointer.symlink_to(store)
        return pointer

    def test_a_pointer_match_delegates_to_the_owner_notice_facade(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given: 활성 포인터가 통지 대상 릴리스를 가리킨다
        monkeypatch.setenv("NODE_RELEASE_CURRENT", str(self._pointer(tmp_path, _SHA)))
        sent: list[str] = []
        monkeypatch.setattr(
            owner_notice, "notify_owner", lambda notice, *, message=None: sent.append(notice) or True
        )

        # When
        exit_code = release_applied_notice.main(
            ["send", "--version", "v1.2.4", "--head", _SHA]
        )

        # Then: 번호는 인자 그대로 쓰고 목적지는 파사드가 정한다
        assert exit_code == 0
        assert sent == [f"릴리스 v1.2.4 가 적용되었습니다. (HEAD {_SHA[:12]})"]

    def test_a2_the_notice_lands_in_the_configured_notice_channel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """2026-09-10 소유자 지시: 적용 완료도 #notifications 에서 나온다.

        목적지를 파사드 밖에서 정하지 않는다 — `notify_owner` 가 `OWNER_NOTICE_CHANNEL_ID`
        를 존중하고, 미설정 설치는 종전대로 소유자 DM 으로 되돌린다(ON-1). 이전 판본은
        `notify_owner_dm` 이라 통지 채널이 설정돼 있어도 **일부러** DM 으로만 갔다.
        """
        # Given: 통지 채널이 설정된 설치 + 이 릴리스를 가리키는 활성 포인터
        monkeypatch.setenv("NODE_RELEASE_CURRENT", str(self._pointer(tmp_path, _SHA)))
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "unit-test-token")
        monkeypatch.setenv("AUTOPHAGY_OWNER_ID", "42")
        monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "1500000000000000002")
        sent: list[tuple[str, str]] = []
        monkeypatch.setattr(
            owner_notice, "send_notice", lambda token, channel, body: sent.append((channel, body))
        )
        monkeypatch.setattr(
            owner_notice,
            "owner_dm_channel",
            lambda token, owner_id: pytest.fail("채널이 지정되면 DM 을 열지 않는다"),
        )

        # When
        exit_code = release_applied_notice.main(["send", "--version", "v1.2.4", "--head", _SHA])

        # Then
        assert exit_code == 0
        assert len(sent) == 1
        channel, body = sent[0]
        assert channel == "1500000000000000002"
        from automation.release_applied_message import applied_message
        from automation.interop.owner_message import Ref, render
        message = applied_message("v1.2.4", _SHA, f"릴리스 v1.2.4 가 적용되었습니다. (HEAD {_SHA[:12]})")
        assert message is not None
        assert body == render(message, destination=Ref(scope="channel", channel_id=channel))

    def test_b_undelivered_notice_is_a_nonzero_exit_so_the_sweep_retries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NODE_RELEASE_CURRENT", str(self._pointer(tmp_path, _SHA)))
        monkeypatch.setattr(owner_notice, "notify_owner", lambda notice, *, message=None: False)

        assert release_applied_notice.main(["send", "--version", "v1.2.4", "--head", _SHA]) == 3

    def test_c_pointer_mismatch_refuses_at_the_node(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Given: 워크스테이션 판정 뒤 노드가 이미 다른 릴리스로 옮겨간 경우
        monkeypatch.setenv("NODE_RELEASE_CURRENT", str(self._pointer(tmp_path, _OTHER)))
        monkeypatch.setattr(
            owner_notice,
            "notify_owner",
            lambda notice: pytest.fail("포인터가 다르면 보내지 않는다"),
        )

        # When
        exit_code = release_applied_notice.main(
            ["send", "--version", "v1.2.4", "--head", _SHA]
        )

        # Then
        assert exit_code == 4
        assert "APPLIED-POINTER-MISMATCH" in capsys.readouterr().err

    def test_d_unreadable_pointer_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("NODE_RELEASE_CURRENT", str(tmp_path / "absent"))
        monkeypatch.setattr(
            owner_notice,
            "notify_owner",
            lambda notice: pytest.fail("포인터를 못 읽으면 보내지 않는다"),
        )

        assert release_applied_notice.main(["send", "--version", "v1.2.4", "--head", _SHA]) == 4
        assert "APPLIED-POINTER-MISMATCH" in capsys.readouterr().err
