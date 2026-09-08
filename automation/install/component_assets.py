"""설치 자산 명세: 사용자 유닛 원본 보존을 위해 릴리스 심볼릭 링크를 사용한다."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from automation.install.components import EnableUserUnit, EnsureSymlink, OptInComponent
from automation.node_asset_renderer import render_asset
from automation.node_config import NodeConfig


@dataclass(frozen=True, slots=True)
class DirectoryState:
    mode: int
    owner: str
    group: str


@dataclass(frozen=True, slots=True)
class FileState:
    digest: str
    mode: int
    owner: str
    group: str


@dataclass(frozen=True, slots=True)
class DirectorySpec:
    path: Path
    mode: int
    owner: str
    group: str

    def state(self) -> DirectoryState:
        return DirectoryState(self.mode, self.owner, self.group)


@dataclass(frozen=True, slots=True)
class FileSpec:
    path: Path
    content: str
    mode: int
    owner: str
    group: str
    #: 운영자가 채운 비밀정보를 다음 설치 실행이 템플릿으로 덮어쓰면 안 된다.
    create_only: bool = False

    def state(self) -> FileState:
        digest = hashlib.sha256(self.content.encode()).hexdigest()
        return FileState(digest, self.mode, self.owner, self.group)


@dataclass(frozen=True, slots=True)
class ComponentAssets:
    files: tuple[FileSpec, ...] = ()
    directories: tuple[DirectorySpec, ...] = ()
    symlinks: tuple[EnsureSymlink, ...] = ()
    user_units: tuple[EnableUserUnit, ...] = ()
    timers: tuple[str, ...] = ()


def build_component_assets(
    repo_root: Path, config: NodeConfig, selected: tuple[OptInComponent, ...],
) -> ComponentAssets:
    files: list[FileSpec] = []
    directories: list[DirectorySpec] = []
    symlinks: list[EnsureSymlink] = []
    user_units: list[EnableUserUnit] = []
    timers: list[str] = []
    for component in selected:
        match component.scope:
            case "system":
                destination = Path("/etc/systemd/system")
                owner = "root"
                timers.extend(component.timers)
            case "user":
                owner = config.ops_account
                destination = config.ops_home / ".config/systemd/user"
                work = config.ops_home / component.name
                directories.extend(DirectorySpec(path, 0o750, owner, owner) for path in (
                    config.ops_home / ".config", destination.parent, destination, work,
                ))
                symlinks.append(EnsureSymlink(work / "automation", config.release_current / "automation", owner))
                template = (
                    "# 인증 정보를 채우기 전에는 대시보드가 시작되지 않는다.\n"
                    "# 필요한 항목의 주석을 해제한다. 선택 항목은 주석 상태여야 기본값을 쓴다.\n"
                    "# DISCORD_BOT_TOKEN은 ~/.env.secrets에서 관리할 수 있다.\n"
                ) + "".join(f"# {name}=\n" for name in component.env_variables)
                files.append(FileSpec(work / "hub.env", template, 0o600, owner, owner, create_only=True))
                user_units.extend(EnableUserUnit(name, owner) for name in component.units)
            case _:
                assert_never(component.scope)
        files.extend(FileSpec(destination / name, render_asset(repo_root / component.source / name, config),
                              0o644, owner, owner) for name in component.units)
    return ComponentAssets(tuple(files), tuple(directories), tuple(symlinks), tuple(user_units), tuple(timers))
