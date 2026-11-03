"""Release-applied notice destination: only the owner line moves (owner 2026-11-03)."""
from __future__ import annotations

import io
import json
import subprocess
from dataclasses import replace
from hashlib import sha256

import pytest

from automation import notice_bot, notice_bot_release, owner_notice, release_applied_notice, release_approval
from tests.unit import test_release_authorize as auth
from tests.unit.test_healthcheck_wrapper_release import _fake_plan, _plan_args, _spec

OWNER = "444444444444444444"
PAIR = "555555555555555555"
APPLIED = "666666666666666666"
TOKEN = "synthetic release notice credential"
DIGEST = sha256(TOKEN.encode()).hexdigest()
HEAD = "c" * 40


def _override(home, **extra):
    path = home / notice_bot.OVERRIDE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": TOKEN, "channel_id": OWNER, **extra}))
    path.chmod(0o600)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    pointer = tmp_path / "current"
    pointer.symlink_to(tmp_path / HEAD)
    monkeypatch.setenv("NODE_RELEASE_CURRENT", str(pointer))
    return tmp_path


def _send(monkeypatch, *pending):
    sent = []
    monkeypatch.setattr(owner_notice, "send_notice", lambda *args: sent.append(args))
    rc = release_applied_notice.main([
        "send", "--version", "v2.7.21", "--head", HEAD, "--summary", "짧은 완료 알림",
        *(("--pending", json.dumps(list(pending))) if pending else ()),
    ])
    return rc, sent


@pytest.mark.parametrize("configured", [True, False])
def test_the_applied_line_takes_the_release_channel_only_when_configured(home, monkeypatch, configured):
    # Given: a node override with or without the release-applied destination.
    _override(home, fault_channel_id=PAIR, **({"release_channel_id": APPLIED} if configured else {}))
    # When: the node sends one applied notice with one operator item.
    rc, sent = _send(monkeypatch, "남은 소유자 조치:", "- allowlist 다시 만들기")
    # Then: operator items still go to the pair, the owner line to the release channel or the old one.
    assert rc == 0
    assert [(token, channel) for token, channel, _ in sent] == [
        (TOKEN, PAIR), (TOKEN, APPLIED if configured else OWNER),
    ]
    assert sent[1][2].startswith("✅ v2.7.21 적용됨 — 짧은 완료 알림")


def test_ordinary_and_fault_notices_ignore_the_release_channel(home, monkeypatch):
    # Given: all three destinations are configured.
    _override(home, fault_channel_id=PAIR, release_channel_id=APPLIED)
    sent = []
    monkeypatch.setattr(owner_notice, "send_notice", lambda *args: sent.append(args))
    # When: an ordinary notice, a fault notice and a fault+release call are sent.
    assert owner_notice.notify_owner("정기 알림")
    assert owner_notice.notify_owner("고장", fault=True)
    assert owner_notice.notify_owner("고장", fault=True, release=True)
    # Then: none of them moves to the release channel.
    assert [channel for _, channel, _ in sent] == [OWNER, PAIR, PAIR]


def test_install_and_readback_bind_the_release_destination(home, monkeypatch):
    # Given: an approved payload carrying the release destination.
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({
        "token": TOKEN, "channel_id": OWNER, "fault_channel_id": PAIR, "release_channel_id": APPLIED,
    })))
    # When: the node installs it.
    assert notice_bot.main(["install", "--digest", DIGEST, "--channel", OWNER,
                            "--fault-channel", PAIR, "--release-channel", APPLIED]) == 0
    # Then: readback is clean only for the exact destination, and the old shape reads as drift.
    assert notice_bot.release_channel(home) == APPLIED
    assert notice_bot.installed(DIGEST, OWNER, PAIR, APPLIED)
    assert not notice_bot.installed(DIGEST, OWNER, PAIR)
    assert not notice_bot.installed(DIGEST, OWNER, PAIR, OWNER)


@pytest.mark.parametrize("extra", [{"release_channel_id": "x" * 18}, {"other_channel_id": APPLIED}])
def test_malformed_override_is_refused(home, extra):
    _override(home, **extra)
    assert notice_bot.read_override(home) is None


def test_release_plan_binds_the_destination_with_one_card_line(monkeypatch, capsys):
    # Given: the notice-bot release plan without the new flag.
    _fake_plan(monkeypatch)
    args = _plan_args()
    args.notice_bot, args.hermes_gateways, args.yoodori_channel = DIGEST, "stopped", OWNER
    assert release_approval.cmd_plan(args) == 0
    baseline = json.loads(capsys.readouterr().out)
    # When: the release-host flag is included.
    args.release_notice_channel = APPLIED
    assert release_approval.cmd_plan(args) == 0
    after = json.loads(capsys.readouterr().out)
    # Then: the id is hash-bound and shows exactly one approval line.
    surface = ["release-notice-channel:" + APPLIED, sha256(APPLIED.encode()).hexdigest()]
    assert surface in after["surface_digests"]
    assert "release-notice-channel:" not in json.dumps(baseline)
    spec = replace(_spec(), surface_digests=tuple(map(tuple, after["surface_digests"])))
    assert spec.render().count(notice_bot.RELEASE_CARD_LINE) == 1


def test_release_plan_refuses_the_destination_without_notice_bot(monkeypatch, capsys):
    _fake_plan(monkeypatch)
    args = _plan_args()
    args.release_notice_channel = APPLIED
    assert release_approval.cmd_plan(args) == 4
    assert "release-notice-channel requires notice-bot" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["", APPLIED + "\n", "off", "x" * 18])
def test_release_flag_refuses_nonliteral_ids(tmp_path, value):
    flag = tmp_path / ".hermes/release-flags/release-notice-channel"
    flag.parent.mkdir(parents=True)
    flag.write_text(value)
    with pytest.raises(ValueError):
        notice_bot_release.release_channel_switch(tmp_path)


def test_absent_flag_is_off(tmp_path):
    assert notice_bot_release.release_channel_switch(tmp_path) == "off"


def test_release_transport_authorizes_before_writing(tmp_path, monkeypatch):
    # Given: a valid sender and approval seam.
    monkeypatch.setattr(notice_bot_release, "source_token", lambda home: TOKEN)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(notice_bot_release.subprocess, "run", run)
    # When: the release installs the optional destination.
    assert notice_bot_release.deploy(tmp_path, "node", "/runtime", "a" * 40, DIGEST, OWNER, "off", APPLIED) == 0
    # Then: both authorizations precede all writes and every account receives the destination.
    assert [call[0][1] for call in calls[:2]] == ["authorize-notice-bot", "authorize-release-notice-channel"]
    assert len(calls[2:]) == 3
    assert all(json.loads(kwargs["input"])["release_channel_id"] == APPLIED for _, kwargs in calls[2:])
    assert all("--release-channel " + APPLIED in call[0][2] for call in calls[2:])


@pytest.mark.parametrize("reaction,channel,head,expected", [
    ("approve", APPLIED, "a" * 40, 0),
    ("pending", APPLIED, "a" * 40, 1),
    ("approve", OWNER, "a" * 40, 4),
    ("approve", APPLIED, "b" * 40, 4),
])
def test_release_surface_requires_approval_for_exact_channel_and_head(
    tmp_path, monkeypatch, reaction, channel, head, expected,
):
    # Given: a stored release that binds exactly one release-applied destination.
    base = auth._spec()
    surface = ("release-notice-channel:" + APPLIED, sha256(APPLIED.encode()).hexdigest())
    spec = replace(base, surface_digests=(*base.surface_digests, surface))
    monkeypatch.setattr(auth, "_spec", lambda: spec)
    owner = [{"id": auth._OWNER, "bot": False}]
    auth._env(tmp_path, monkeypatch, reactions={
        auth._APPROVE: owner if reaction == "approve" else [], auth._CANCEL: [],
    }, render_version=6)
    # When/Then: only an authorized release covers this destination.
    assert release_approval.main([
        "authorize-release-notice-channel", "--channel-id", channel, "--head", head,
    ]) == expected
