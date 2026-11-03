"""도리 herdr 백엔드 설치 — `dori.py` 가 `--backend herdr`(기본)일 때 부른다.

`dori/herdr.toml` 에 고정한 herdr·ttyd 를 받아 sha256 을 확인하고 계정의 `~/.local` 에 둔다. 포크의 유닛으로
herdr 서버(`dori-herdr.service`)와 웹(`herdr-web@<포트>`)을 켜고, 새로 복사한 config 이면 리드를 herdr pane 에
띄워 `leadPane` 에 적는다. root 는 쓰지 않는다. 이미 있는 파일은 덮지 않는다 — 다른 herdr·ttyd·유닛·웹 설정이
있으면 그대로 두고 그렇다고 말한다. 다른 프로그램(옛 aoe 웹)이 웹 포트를 쓰면 웹만 켜지 않고 넘어간다.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import socket
import subprocess
import time
import tomllib
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

TOOLS_RELPATH: Final = Path("dori") / "herdr.toml"
_FORK_SETUPS: Final = Path("skills") / "dori-mode" / "setups"
_SERVER_UNIT: Final = "dori-herdr.service"
_EXAMPLE_CWD: Final = "~/code/my-project"
_READY_TRIES: Final = 50
_AGENT_TRIES: Final = 60

Capture = Callable[[tuple[str, ...]], tuple[int, str]]
Fetch = Callable[[str, Path], None]
Which = Callable[[str], str | None]


class HerdrInstallError(RuntimeError):
    """herdr 설치를 멈출 이유 — 메시지 첫 낱말이 판정 표식이다."""


@dataclass(frozen=True, slots=True)
class Asset:
    url: str
    sha256: str


@dataclass(frozen=True, slots=True)
class Tools:
    herdr_version: str
    herdr: Mapping[str, Asset]
    ttyd_version: str
    ttyd: Mapping[str, Asset]
    web_port: int
    ttyd_port: int
    state_port: int


def load_tools(root: Path) -> Tools:
    data = tomllib.loads((root / TOOLS_RELPATH).read_text(encoding="utf-8"))
    def assets(table: Mapping[str, Mapping[str, str]]) -> dict[str, Asset]:
        return {key: Asset(v["url"], v["sha256"]) for key, v in table.items()}
    web = data["web"]
    return Tools(data["herdr"]["version"], assets(data["herdr"]["assets"]), data["ttyd"]["version"],
                 assets(data["ttyd"]["assets"]), int(web["port"]), int(web["ttyd_port"]), int(web["state_port"]))


def platform_key(system: str | None = None, machine: str | None = None) -> str:
    os_name = {"Linux": "linux", "Darwin": "macos"}.get(system or platform.system(), (system or platform.system()).lower())
    arch = {"amd64": "x86_64", "arm64": "aarch64"}.get((machine or platform.machine()).lower(), (machine or platform.machine()).lower())
    return f"{os_name}-{arch}"


@dataclass(frozen=True, slots=True)
class Layout:
    home: Path
    src: Path
    web_port: int

    @property
    def herdr(self) -> Path: return self.home / ".local" / "lib" / "herdr" / "herdr"
    @property
    def herdr_link(self) -> Path: return self.home / ".local" / "bin" / "herdr"
    @property
    def guard(self) -> Path: return self.src / _FORK_SETUPS / "discord-aoe" / "herdr-guard"
    @property
    def ttyd(self) -> Path: return self.home / ".local" / "bin" / "ttyd"
    @property
    def herdr_config(self) -> Path: return self.home / ".config" / "herdr" / "config.toml"
    @property
    def units(self) -> Path: return self.home / ".config" / "systemd" / "user"
    @property
    def server_unit(self) -> Path: return self.src / _FORK_SETUPS / "discord-aoe" / "systemd" / _SERVER_UNIT
    @property
    def web_units(self) -> tuple[Path, ...]: return tuple(sorted((self.src / _FORK_SETUPS / "herdr-web").glob("herdr-web*@.service")))
    @property
    def web_env(self) -> Path: return self.home / ".config" / "herdr" / f"web-{self.web_port}.env"
    @property
    def aoe_serve_env(self) -> Path: return self.home / ".config" / "agent-of-empires" / "serve.env"


def plan(layout: Layout, tools: Tools, key: str) -> list[tuple[str, str]]:
    herdr = tools.herdr.get(key)
    ttyd = tools.ttyd.get(key)
    return [
        ("herdr-bin", f"{layout.herdr} herdr {tools.herdr_version} ({key}{', sha256 ' + herdr.sha256[:12] if herdr else ' — 받을 바이너리 없음, 건너뜀'}), {layout.herdr_link} -> 포크 herdr-guard"),
        ("herdr-ttyd", f"{layout.ttyd} ttyd {tools.ttyd_version} ({key}{', sha256 ' + ttyd.sha256[:12] if ttyd else ' — 받을 바이너리 없음, 웹 건너뜀'})"),
        ("herdr-server", f"{layout.units / _SERVER_UNIT} 를 켠다(systemctl --user, 이미 있으면 그대로)"),
        ("herdr-web", f"herdr-web@{layout.web_port} — {layout.web_env}(0600, 없을 때만) · 포트를 다른 프로그램이 쓰면 켜지 않음"),
        ("herdr-lead", "새로 복사한 config 이면 리드를 herdr pane 에 띄우고 leadPane 에 적는다"),
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def default_fetch(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as response, dest.open("wb") as out:  # noqa: S310 - fixed https URLs from dori/herdr.toml
        while block := response.read(1 << 20):
            _ = out.write(block)


def default_capture(argv: tuple[str, ...]) -> tuple[int, str]:
    # a shell inside a herdr pane carries HERDR_SOCKET_PATH and friends, which would aim herdr at that pane's server
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERDR_")}
    done = subprocess.run(argv, check=False, capture_output=True, text=True, env=env)
    return done.returncode, done.stdout + done.stderr


def install_binary(dest: Path, asset: Asset, fetch: Fetch) -> str:
    if dest.exists():
        return "same" if _sha256(dest) == asset.sha256 else "kept"
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        fetch(asset.url, part)
        got = _sha256(part)
        if got != asset.sha256:
            raise HerdrInstallError(f"DORI-HERDR-SHA-MISMATCH: {asset.url} sha256 {got}, want {asset.sha256} — nothing installed")
        part.chmod(0o755)
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    return "installed"


def _copy_once(source: Path, dest: Path) -> str:
    content = source.read_bytes()
    if dest.exists():
        return "same" if dest.read_bytes() == content else "differs"
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = dest.write_bytes(content)
    return "new"


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _web_env(layout: Layout, tools: Tools, origin: str) -> str:
    lines = [
        f"HERDR_TTYD_PORT={tools.ttyd_port}",
        f"HERDR_WEB_ORIGIN={origin}",
        f"HERDR_STATE_PORT={tools.state_port}",
        f"HERDR_STATE_SECRET={secrets.token_hex(32)}",
    ]
    if not layout.aoe_serve_env.exists():
        lines.append(f"HERDR_WEB_PASSPHRASE={secrets.token_hex(16)}")
    return "\n".join(lines) + "\n"


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        _ = handle.write(text)


def _systemctl(capture: Capture, *args: str) -> tuple[int, str]:
    return capture(("systemctl", "--user", *args))


def open_lead(layout: Layout, config: Path, lead_dir: Path, label: str, capture: Capture, which: Which,
              sleep: Callable[[float], None]) -> None:
    data = json.loads(config.read_text(encoding="utf-8"))
    current = str(data.get("leadPane", ""))
    if data.get("backend") != "herdr" or (current and not current.startswith("<")):
        print(f"DORI-LEAD-KEEP {config} backend={data.get('backend')} leadPane={current or '-'} — 리드는 그대로 둔다")
        return
    herdr = str(layout.herdr)
    lead_dir.mkdir(parents=True, exist_ok=True)
    code, out = capture((herdr, "workspace", "create", "--label", label, "--cwd", str(lead_dir), "--no-focus"))
    if code != 0:
        print(f"DORI-LEAD-FAIL herdr workspace create: {out.strip()[:200]}")
        return
    pane = json.loads(out)["result"]["root_pane"]["pane_id"]
    for _ in range(_READY_TRIES):
        if capture((herdr, "pane", "read", pane, "--source", "visible"))[1].strip():
            break
        sleep(0.2)
    data["leadPane"] = pane
    if data.get("defaultCwd") == _EXAMPLE_CWD:
        data["defaultCwd"] = str(lead_dir)
    _ = config.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    agent = str((data.get("agentCommand") or ["omo"])[0])
    if which(agent) is None:
        print(f"DORI-LEAD-PANE {pane} — {agent} 가 PATH 에 없어 리드 에이전트는 켜지 않았다. 깐 뒤: herdr pane run {pane} '{agent} --session-id <새 uuid>'")
        return
    session = str(uuid.uuid4())
    code, out = capture((herdr, "pane", "run", pane, f"{agent} --session-id {session}"))
    if code != 0:
        print(f"DORI-LEAD-FAIL herdr pane run {pane}: {out.strip()[:200]}")
        return
    screen = ""
    for _ in range(_AGENT_TRIES):
        screen = capture((herdr, "pane", "read", pane, "--source", "visible"))[1]
        if _omo_input_box(screen):
            print(f"DORI-LEAD-STARTED pane={pane} session={session} cwd={lead_dir}")
            return
        sleep(1.0)
    tail = " | ".join(line.strip() for line in screen.strip().splitlines()[-3:])
    print(f"DORI-LEAD-NOT-READY pane={pane}: {agent} 입력창이 {_AGENT_TRIES}초 안에 뜨지 않았다(화면 끝: {tail[:200]}) — "
          f"고친 뒤 herdr pane run {pane} '{agent} --session-id {session}'")


def _omo_input_box(screen: str) -> bool:
    # omo's own input box: a ❯ line with the box's ─ border right above it (a shell prompt drawn as ❯ has no box);
    # the same rule the fork's reviveHerdr waits on
    lines = screen.split("\n")
    return any(line.lstrip().startswith("❯") and any(up.lstrip().startswith("─") for up in lines[max(0, i - 2):i])
               for i, line in enumerate(lines))


def install(layout: Layout, tools: Tools, key: str, *, origin: str, config: Path, lead_dir: Path, lead_label: str,
            capture: Capture, fetch: Fetch, which: Which, sleep: Callable[[float], None] = time.sleep) -> int:
    herdr_asset = tools.herdr.get(key)
    if herdr_asset is None:
        print(f"DORI-HERDR-UNSUPPORTED {key}: herdr {tools.herdr_version} 바이너리가 없다 — herdr 단계를 건너뛴다 (https://herdr.dev)")
        return 0
    try:
        print(f"DORI-HERDR-BIN {layout.herdr} {install_binary(layout.herdr, herdr_asset, fetch)}")
        if layout.herdr_link.exists() and not layout.herdr_link.is_symlink():
            print(f"DORI-HERDR-KEEP {layout.herdr_link} 은 링크가 아닌 herdr 다 — 그대로 둔다(옮기면 가드가 붙는다: {layout.herdr})")
        else:
            layout.herdr_link.parent.mkdir(parents=True, exist_ok=True)
            layout.herdr_link.unlink(missing_ok=True)
            layout.herdr_link.symlink_to(layout.guard)
        ttyd_asset = tools.ttyd.get(key)
        ttyd = install_binary(layout.ttyd, ttyd_asset, fetch) if ttyd_asset else "unsupported"
        print(f"DORI-HERDR-TTYD {layout.ttyd} {ttyd}")
    except (HerdrInstallError, OSError) as error:
        print(error if isinstance(error, HerdrInstallError) else f"DORI-HERDR-FETCH-FAIL: {error}")
        return 5
    if not layout.herdr_config.exists():
        layout.herdr_config.parent.mkdir(parents=True, exist_ok=True)
        _ = layout.herdr_config.write_text("onboarding = false\n", encoding="utf-8")

    if _systemctl(capture, "show-environment")[0] != 0:
        print("DORI-HERDR-NO-SYSTEMD: systemctl --user 를 쓸 수 없어 서버·웹 유닛과 리드를 건너뛴다 — `herdr` 를 열면 서버가 뜬다")
        return 0
    for source in (layout.server_unit, *layout.web_units):
        state = _copy_once(source, layout.units / source.name)
        print(f"DORI-HERDR-UNIT {layout.units / source.name} {state}")
    web_env = "kept" if layout.web_env.exists() else "new"
    if web_env == "new":
        _write_private(layout.web_env, _web_env(layout, tools, origin))
    print(f"DORI-HERDR-WEB-ENV {layout.web_env} {web_env}")
    _ = _systemctl(capture, "daemon-reload")
    code, out = _systemctl(capture, "enable", "--now", _SERVER_UNIT)
    if code != 0:
        print(f"DORI-HERDR-SERVER-FAIL: {out.strip()[:200]}")
        return 6
    for _ in range(_READY_TRIES):
        if capture((str(layout.herdr), "pane", "list"))[0] == 0:
            break
        sleep(0.2)
    else:
        print(f"DORI-HERDR-SERVER-FAIL: {_SERVER_UNIT} 가 켜졌지만 herdr 가 답하지 않는다")
        return 6
    print(f"DORI-HERDR-SERVER {_SERVER_UNIT} active")

    web = f"herdr-web@{layout.web_port}.service"
    if ttyd == "unsupported":
        print(f"DORI-HERDR-WEB-SKIP {key}: ttyd 바이너리가 없다")
    elif _systemctl(capture, "is-active", web)[0] == 0:
        print(f"DORI-HERDR-WEB {web} 이미 켜짐")
    elif not port_free(layout.web_port):
        print(f"DORI-HERDR-WEB-PORT-BUSY {layout.web_port}: 다른 프로그램(옛 aoe 웹 등)이 쓴다 — 그것을 멈춘 뒤 systemctl --user enable --now {web} (docs/guide/herdr-전환.md)")
    else:
        code, out = _systemctl(capture, "enable", "--now", web)
        print(f"DORI-HERDR-WEB {web} {'active' if code == 0 else 'FAIL ' + out.strip()[:200]} — {origin}")
    open_lead(layout, config, lead_dir, lead_label, capture, which, sleep)
    return 0
