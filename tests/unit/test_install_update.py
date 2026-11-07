"""도리 기본 설치의 업데이트 — 서명된 최신 릴리스로만 옮기고, 처음 신뢰는 공지 지문과 같은 키만 들인다.

윈도우·리눅스 계정 설치가 같은 명령(`python -m automation.install.update`)으로 새 판을 받는다. 노드 리컨실러와 같은
서명·바닥 규칙을 쓰므로 서명 없는 더 높은 태그, 지문이 다른 키, 고친 체크아웃은 아무것도 옮기지 않아야 한다.
원격은 로컬 bare 저장소, 서명은 여기서 만든 ssh 키, 도리 설치기 실행은 기록만 하는 가짜가 대신한다.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from automation.install import update
from automation.install.allowed_signers import fingerprint, parse_public_key


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ("git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args),
        cwd=cwd, capture_output=True, text=True, check=False, timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


class Channel:
    def __init__(self, tmp_path: Path) -> None:
        self.key = tmp_path / "update-trust"
        _ = subprocess.run(("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)), check=True)
        self.fingerprint = fingerprint(parse_public_key(self.key.with_suffix(".pub").read_text(encoding="utf-8")))
        self.remote = tmp_path / "origin.git"
        self.publisher = tmp_path / "publisher"
        self.checkout = tmp_path / "home" / ".cytoplasm" / "src"
        _ = _git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(self.remote))
        _ = _git(tmp_path, "init", "--quiet", "-b", "main", str(self.publisher))
        _ = _git(self.publisher, "remote", "add", "origin", str(self.remote))

    def release(self, tag: str, *, signed: bool = True) -> str:
        (self.publisher / "release.txt").write_text(tag + "\n", encoding="utf-8")
        _ = _git(self.publisher, "add", "release.txt")
        _ = _git(self.publisher, "commit", "--quiet", "-m", tag)
        if signed:
            _ = _git(self.publisher, "-c", "gpg.format=ssh", "-c", f"user.signingKey={self.key}", "tag", "-s", tag, "-m", tag)
        else:
            _ = _git(self.publisher, "tag", "-a", tag, "-m", tag)
        _ = _git(self.publisher, "push", "--quiet", "origin", "main", tag)
        return _git(self.publisher, "rev-parse", "HEAD")

    def clone_at(self, tag: str) -> None:
        _ = _git(self.publisher.parent, "clone", "--quiet", str(self.remote), str(self.checkout))
        _ = _git(self.checkout, "checkout", "--quiet", "--detach", tag)


@pytest.fixture
def channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Channel:
    made = Channel(tmp_path)
    monkeypatch.setattr(update, "REPO_ROOT", made.checkout)
    return made


@pytest.fixture
def installs(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(update, "run_dori", lambda args: calls.append(list(args)) or 0)
    return calls


def _main(channel: Channel, *extra: str) -> int:
    return update.main(("--state", str(channel.checkout.parent), *extra))


def _first(channel: Channel, *extra: str) -> int:
    return _main(channel, "--trust-key", str(channel.key.with_suffix(".pub")), "--expect-fingerprint", channel.fingerprint, *extra)


def test_first_run_trusts_the_published_key_and_moves_to_the_newest_signed_release(
    channel: Channel, installs: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    _ = channel.release("v1.0.0")
    channel.clone_at("v1.0.0")
    newest = channel.release("v1.1.0")

    assert _first(channel, "--role", "okdori", "--owner-call", "형") == 0

    out = capsys.readouterr().out
    assert _git(channel.checkout, "rev-parse", "HEAD") == newest
    assert "CYTO-UPDATE-MOVED" in out and "CYTO-UPDATED v1.1.0 rc=0" in out
    assert installs == [["--role", "okdori", "--owner-call", "형"]]
    signers = (channel.checkout.parent / update.SIGNERS_NAME).read_text(encoding="utf-8")
    assert signers.startswith('update-trust@autophagy namespaces="git" ssh-ed25519 ')
    assert json.loads((channel.checkout.parent / update.FLOOR_NAME).read_text(encoding="utf-8"))["tag"] == "v1.1.0"


def test_a_key_whose_fingerprint_is_not_the_published_one_changes_nothing(
    channel: Channel, installs: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    old = channel.release("v1.0.0")
    channel.clone_at("v1.0.0")
    _ = channel.release("v1.1.0")

    assert _main(channel, "--trust-key", str(channel.key.with_suffix(".pub")), "--expect-fingerprint", "SHA256:" + "A" * 43) == 2

    assert "CYTO-UPDATE-FINGERPRINT-MISMATCH" in capsys.readouterr().out
    assert _git(channel.checkout, "rev-parse", "HEAD") == old
    assert not (channel.checkout.parent / update.SIGNERS_NAME).exists()
    assert installs == []


def test_a_newer_tag_without_the_trusted_signature_is_not_taken(
    channel: Channel, installs: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    signed = channel.release("v1.0.0")
    channel.clone_at("v1.0.0")
    _ = channel.release("v9.9.9", signed=False)

    assert _first(channel) == 0

    assert "CYTO-UPDATE-CURRENT v1.0.0" in capsys.readouterr().out
    assert _git(channel.checkout, "rev-parse", "HEAD") == signed
    assert installs == [[]]


def test_a_release_older_than_one_already_installed_is_refused(
    channel: Channel, installs: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    _ = channel.release("v1.0.0")
    channel.clone_at("v1.0.0")
    _ = channel.release("v2.0.0")
    assert _first(channel) == 0
    _ = _git(channel.remote, "tag", "-d", "v2.0.0")
    _ = capsys.readouterr()

    assert _main(channel) == 2

    assert "CYTO-UPDATE-UNTRUSTED: RELEASE-ROLLBACK" in capsys.readouterr().out
    assert len(installs) == 1


def test_an_edited_checkout_is_left_alone(channel: Channel, installs: list[list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    old = channel.release("v1.0.0")
    channel.clone_at("v1.0.0")
    _ = channel.release("v1.1.0")
    (channel.checkout / "release.txt").write_text("mine\n", encoding="utf-8")

    assert _first(channel) == 2

    assert "CYTO-UPDATE-DIRTY" in capsys.readouterr().out
    assert _git(channel.checkout, "rev-parse", "HEAD") == old
    assert installs == []


def test_without_a_trust_file_or_a_key_it_says_how_to_start(
    channel: Channel, installs: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    _ = channel.release("v1.0.0")
    channel.clone_at("v1.0.0")

    assert _main(channel) == 2

    assert "CYTO-UPDATE-NO-TRUST" in capsys.readouterr().out
    assert installs == []
