"""네이티브 윈도우 도리 설치와 고정 포크 옮기기 — 리눅스 CI 에서 돌 수 있는 부분.

윈도우 실기 검증(작업 스케줄러·junction·PATH)은 CI 의 windows-install 잡(tests/windows/install-update.ps1)이 한다.
여기서는 그 잡이 기대는 계약을 가짜 capture 로 고정한다: herdr zip 은 zip 의 sha256 이 맞을 때만 풀리고, 윈도우
계획은 systemd 대신 두 로그온 작업을 말하고, --update 의 재시작은 작업 호스트를 멈춘 뒤 남은 bun 트리를 끝내고 다시
켜며, 포크 업데이트는 설치기가 받은 깨끗한 detach 체크아웃만 옮긴다.
"""
from __future__ import annotations

import hashlib
import io
import subprocess
import zipfile
from pathlib import Path

import pytest

from automation.dori_pin import DoriPin, PinError, fetch_at, move_to
from automation.install import dori_herdr

_GIT = ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false")


def _origin(tmp_path: Path) -> tuple[Path, list[str]]:
    origin = tmp_path / "fork"
    _ = subprocess.run(("git", "init", "--quiet", "-b", "main", str(origin)), check=True)
    shas = []
    for text in ("one", "two"):
        (origin / "README.md").write_text(text + "\n", encoding="utf-8")
        _ = subprocess.run(("git", "-C", str(origin), "add", "."), check=True)
        _ = subprocess.run(("git", *_GIT, "-C", str(origin), "commit", "--quiet", "-m", text), check=True)
        shas.append(subprocess.run(("git", "-C", str(origin), "rev-parse", "HEAD"), check=True,
                                   capture_output=True, text=True).stdout.strip())
    return origin, shas


def test_move_takes_an_installer_checkout_to_the_new_pin(tmp_path: Path) -> None:
    origin, (first, second) = _origin(tmp_path)
    src = tmp_path / "src"
    fetch_at(DoriPin(str(origin), first), src)

    assert move_to(DoriPin(str(origin), second), src) == first

    assert (src / "README.md").read_text(encoding="utf-8") == "two\n"


@pytest.mark.parametrize("state", ["branch", "edited"])
def test_move_leaves_a_checkout_somebody_works_in(tmp_path: Path, state: str) -> None:
    origin, (first, second) = _origin(tmp_path)
    src = tmp_path / "src"
    if state == "branch":
        _ = subprocess.run(("git", "clone", "--quiet", "--branch", "main", str(origin), str(src)), check=True)
        _ = subprocess.run(("git", "-C", str(src), "reset", "--quiet", "--hard", first), check=True)
    else:
        fetch_at(DoriPin(str(origin), first), src)
        (src / "README.md").write_text("mine\n", encoding="utf-8")

    with pytest.raises(PinError, match="DORI-PIN-UPDATE-REFUSED"):
        _ = move_to(DoriPin(str(origin), second), src)

    head = subprocess.run(("git", "-C", str(src), "rev-parse", "HEAD"), capture_output=True, text=True, check=True).stdout.strip()
    assert head == first


def test_windows_maps_to_the_pinned_herdr_zip() -> None:
    tools = dori_herdr.load_tools(Path(dori_herdr.__file__).resolve().parents[2])

    assert dori_herdr.platform_key("Windows", "AMD64") == "windows-x86_64"
    assert tools.herdr["windows-x86_64"].url.endswith("/v0.9.5/herdr-windows-x86_64.zip")
    assert "windows-x86_64" not in tools.ttyd


def _zip(names: tuple[str, ...]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name in names:
            bundle.writestr(name, name)
    return buffer.getvalue()


def test_the_zip_is_unpacked_only_when_its_sha256_matches(tmp_path: Path) -> None:
    payload = _zip(("herdr.exe", "conpty/conpty.dll"))
    dest = tmp_path / ".local" / "lib" / "herdr" / "herdr.exe"
    fetch = lambda _url, path: path.write_bytes(payload)  # noqa: E731
    good = dori_herdr.Asset("https://example.invalid/h.zip", hashlib.sha256(payload).hexdigest())

    with pytest.raises(dori_herdr.HerdrInstallError, match="DORI-HERDR-SHA-MISMATCH"):
        _ = dori_herdr.install_binary(dest, dori_herdr.Asset(good.url, "0" * 64), fetch)
    assert not dest.parent.exists()

    assert dori_herdr.install_binary(dest, good, fetch) == "installed"
    assert (dest.parent / "conpty" / "conpty.dll").is_file()
    assert dori_herdr.install_binary(dest, good, fetch) == "same"


def test_the_windows_plan_names_the_logon_tasks_not_systemd(tmp_path: Path) -> None:
    layout = dori_herdr.Layout(tmp_path, tmp_path / "src", 8181, windows=True)
    tools = dori_herdr.load_tools(Path(dori_herdr.__file__).resolve().parents[2])

    rows = dict(dori_herdr.plan(layout, tools, "windows-x86_64"))

    assert layout.herdr.name == "herdr.exe"
    assert dori_herdr.SERVER_TASK in rows["herdr-server"] and dori_herdr.SERVE_TASK in rows["serve-task"]
    assert "systemctl" not in " ".join(rows.values())


class _Windows:
    """A Windows host as the installer sees it: herdr answers once its task started; dori serve may be running."""

    def __init__(self, serving: bool) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.server_up = False
        self.serving = serving

    def __call__(self, argv: tuple[str, ...]) -> tuple[int, str]:
        self.calls.append(argv)
        if argv[0] == "powershell":
            script = dori_herdr.base64.b64decode(argv[-1]).decode("utf-16-le")
            if "Start-ScheduledTask -TaskName 'Dori herdr server'" in script:
                self.server_up = True
            if ".Count" in script:
                return 0, "1\n" if self.serving else "0\n"
            return 0, ""
        if argv[1:] == ("pane", "list"):
            return (0, "[]") if self.server_up else (1, "server_not_running")
        if argv[2:] == ("serve", "--install-task"):
            return 0, 'SERVE_TASK_INSTALLED "Dori serve" at logon\n'
        return 0, ""

    def scripts(self) -> list[str]:
        return [dori_herdr.base64.b64decode(a[-1]).decode("utf-16-le") for a in self.calls if a[0] == "powershell"]


@pytest.mark.parametrize(("serving", "restart"), [(False, False), (True, True), (True, False)])
def test_supervision_registers_both_tasks_and_restarts_serve_only_on_update(
    tmp_path: Path, serving: bool, restart: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    host = _Windows(serving)
    layout = dori_herdr.Layout(tmp_path, tmp_path / "src", 8181, windows=True)

    assert dori_herdr.windows_supervise(layout, host, lambda _t: "C:/bun.exe", lambda _s: None, restart=restart) == 0

    scripts = host.scripts()
    assert any("Register-ScheduledTask -TaskName 'Dori herdr server'" in s and "-AtLogOn" in s for s in scripts)
    assert ("C:/bun.exe", str(layout.dori_cli), "serve", "--install-task") in host.calls
    killed = any("taskkill.exe" in s and "Stop-ScheduledTask -TaskName 'Dori serve'" in s for s in scripts)
    started = any(s == "Start-ScheduledTask -TaskName 'Dori serve'" for s in scripts)
    assert killed is (serving and restart)
    assert started is (not serving or restart)
    assert "DORI-HERDR-SERVER 'Dori herdr server' running" in capsys.readouterr().out
