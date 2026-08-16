"""provision-skill-roots.sh — Hermes 스킬 루트 토폴로지 **반전**의 회귀 고정 (SS-1).

지금까지 `/home/agent/.hermes/skills` 는 `/srv/autophagy-skills/live` 의 read-only bind
였다(은퇴한 부트스트랩이 fstab 두 줄로 고정해 두었다). 그래서 Hermes 가 자기 1차 루트에
써야 하는 것들(`.usage.json`·`.curator_state`·`.archive`)을 agent 계정에서 **아예 쓸 수
없었다**. Hermes v0.18.2 는 새 스킬을 `HERMES_HOME/skills` 에만 만들고, `skills.external_dirs`
는 1차 루트 뒤에 스캔되는 **읽기 전용 발견 목록**이다(`agent/skill_utils.py:503-511`).
따라서 방향을 뒤집는다 — 1차 루트는 agent 소유 쓰기 가능(0700), live 는 external_dirs.

여기 고정하는 계약:
  1. ro bind 를 만들지 않는다. 레거시 마운트·fstab 두 줄은 **비파괴로 걷어낸다**.
  2. 새 1차 루트는 agent 소유 0700.
  3. hub 상태는 `mv` 로 `.hub` 에 이관하고 `taps.json` 은 sha256 readback 으로 확인한다
     (원본 디렉터리는 절대 `rm -rf` 하지 않는다).
  4. config 는 `skills:` 블록이 없을 때만 정본 블록을 덧붙이고, 부분 블록이면
     **fail-closed** 로 멈춘다(노드의 실제 config 가 바로 이 경우다).
  5. peer 는 `guard_agent_created` 만 받고, 잔여물 제거는 **검증된 것만**, pin 은 readback.

헤르메틱: `test_provision_release_store.py` 의 관용구를 따라 tmp_path 아래 가짜 노드
세계를 만들고 `install`/`mountpoint`/`umount`/`systemctl`/`sudo` 를 export 된 bash 함수로
가로챈다. `hermes` 만은 `sudo ... env ... hermes` 로 exec 되므로 실행 파일 스텁으로 둔다.
"""
from __future__ import annotations

import getpass
import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Sequence

_REPO: Final = Path(__file__).resolve().parents[2]
_PROVISION: Final = _REPO / "automation" / "provision-skill-roots.sh"
_RETIRED: Final = _REPO / "automation" / "provision-readonly-skills.sh"

#: 은퇴 참조 스캔의 예외 — 이유가 없으면 넣지 않는다.
_RETIREMENT_EXEMPT: Final = (
    # 이 파일 자신: 부재를 단언하려면 은퇴한 경로를 이름으로 들고 있어야 한다.
    "tests/unit/test_selfskill_provision.py",
    # 스킬 배포 경계 과제(`test_skill_deploy_boundary.py`)는 이미 자기 참조를 걷어냈으므로
    # 예외가 필요 없다 — 죽은 예외는 넣지 않는다.
)

_PEER_PINS: Final = ("autophagy-interop", "skill-deploy-review")
#: 노드에 실제로 남아 있는 잔여 사본 이름 + 허용목록 밖 대조군.
_PEER_RESIDUE: Final = ("coordination", "wiki", "prompt", "apple")

_BASE_CONFIG: Final = """model:
  provider: custom:litellm
  default: glm-main
timezone: Asia/Seoul
discord:
  require_mention: true
"""
#: 노드 실측(2026-08-15): agent config 에는 이미 `skills:` 가 있고 이 한 줄만 들어 있다.
_PARTIAL_SKILLS: Final = "skills:\n  creation_nudge_interval: 15\n"

_HERMES_STUB: Final = r"""#!/usr/bin/env bash
# 테스트 전용 hermes 스텁: 1차 루트 → external_dirs 순 발견, pin 은 쓰기 가능한 루트에 남는다.
set -euo pipefail
printf 'hermes %s\n' "$*" >> "${SHIM_LOG:?}"
skills_root="$HOME/.hermes/skills"
pins="$skills_root/.pins"
case "${1:-} ${2:-}" in
  "skills list")
    ls -1 "$skills_root" 2>/dev/null | grep -v '^\.' || true
    grep -E '^[[:space:]]+-[[:space:]]*/' "$HOME/.hermes/config.yaml" 2>/dev/null \
      | sed -E 's#^[[:space:]]+-[[:space:]]*##' \
      | while read -r external; do ls -1 "$external" 2>/dev/null || true; done
    ;;
  "curator pin")
    touch "$pins"
    grep -Fxq "${3:?}" "$pins" || printf '%s\n' "${3:?}" >> "$pins"
    ;;
  "curator status")
    [[ -s "$pins" ]] || exit 0
    printf 'pinned (%s): %s\n' "$(wc -l < "$pins" | tr -d ' ')" "$(paste -sd', ' "$pins")"
    ;;
  *) printf 'stub-hermes: unsupported %s\n' "$*" >&2; exit 64 ;;
esac
"""

_SHIMS: Final = r"""
install() {
  printf 'install %s\n' "$*" >> "$SHIM_LOG"
  local -a args=()
  while (( $# > 0 )); do
    case "$1" in
      -o|-g) shift 2 ;;
      *) args+=("$1"); shift ;;
    esac
  done
  command install "${args[@]}"
}
visudo() { return 0; }
mountpoint() {
  local target="${@: -1}" entry
  for entry in ${FAKE_MOUNTED//:/ }; do
    [[ "$entry" == "$target" ]] && return 0
  done
  return 1
}
umount() {
  local target="${@: -1}"
  printf 'umount %s\n' "$target" >> "$SHIM_LOG"
  FAKE_MOUNTED=":$FAKE_MOUNTED:"
  FAKE_MOUNTED="${FAKE_MOUNTED//:$target:/:}"
  FAKE_MOUNTED="${FAKE_MOUNTED#:}"
  FAKE_MOUNTED="${FAKE_MOUNTED%:}"
  export FAKE_MOUNTED
}
systemctl() { printf 'systemctl %s\n' "$*" >> "$SHIM_LOG"; }
sudo() {
  while (( $# > 0 )); do
    case "$1" in
      -n|-H) shift ;;
      -u) shift 2 ;;
      *) break ;;
    esac
  done
  "$@"
}
export -f install visudo mountpoint umount systemctl sudo
"""


@dataclass(frozen=True, slots=True)
class _World:
    """tmp_path 아래에 재현한 가짜 노드 — 실제 경로 구조를 그대로 흉내낸다."""

    root: Path

    @property
    def agent_home(self) -> Path:
        return self.root / "home" / "agent"

    @property
    def peer_home(self) -> Path:
        return self.root / "home" / "peer"

    @property
    def agent_config(self) -> Path:
        return self.agent_home / ".hermes" / "config.yaml"

    @property
    def peer_config(self) -> Path:
        return self.peer_home / ".hermes" / "config.yaml"

    @property
    def agent_skills(self) -> Path:
        return self.agent_home / ".hermes" / "skills"

    @property
    def peer_skills(self) -> Path:
        return self.peer_home / ".hermes" / "skills"

    @property
    def hub_state(self) -> Path:
        return self.agent_home / ".hermes" / "skill-hub-state"

    @property
    def hub_target(self) -> Path:
        return self.agent_skills / ".hub"

    @property
    def live(self) -> Path:
        return self.root / "srv" / "autophagy-skills" / "live"

    @property
    def fstab(self) -> Path:
        return self.root / "etc" / "fstab"

    @property
    def log(self) -> Path:
        return self.root / "shim.log"

    @property
    def bin(self) -> Path:
        return self.root / "bin"

    @property
    def legacy_bind(self) -> str:
        return f"{self.live} {self.agent_skills} none bind,ro,nosuid,nodev 0 0"

    @property
    def legacy_hub(self) -> str:
        return (
            f"{self.hub_state} {self.hub_target} none bind,rw,nosuid,nodev,noexec 0 0"
        )


def _skill(directory: Path, name: str, *, author: bool) -> None:
    (directory / name).mkdir(parents=True, exist_ok=True)
    header = f"---\nname: {name}\nversion: 1.0.0\n"
    if author:
        header += "author: autophagy-agents\n"
    (directory / name / "SKILL.md").write_text(header + "---\n", encoding="utf-8")


def _world(tmp_path: Path) -> _World:
    """레거시 상태(ro bind + fstab 두 줄 + hub 상태 + peer 잔여물)의 노드를 재현한다."""
    world = _World(tmp_path)
    world.agent_config.parent.mkdir(parents=True)
    world.peer_skills.mkdir(parents=True)
    world.hub_state.mkdir(parents=True)
    world.live.mkdir(parents=True)
    world.bin.mkdir()
    world.fstab.parent.mkdir(parents=True)

    world.agent_config.write_text(_BASE_CONFIG, encoding="utf-8")
    world.peer_config.write_text(_BASE_CONFIG, encoding="utf-8")
    (world.hub_state / "taps.json").write_text('{"taps": ["autophagy"]}\n', encoding="utf-8")
    (world.hub_state / "audit.log").write_text("installed wiki\n", encoding="utf-8")

    for name in ("wiki", "coordination", "prompt"):
        _skill(world.live, name, author=True)
    for name in _PEER_RESIDUE:
        _skill(world.peer_skills, name, author=name != "prompt")
    for name in _PEER_PINS:
        _skill(world.peer_skills, name, author=False)

    world.fstab.write_text(
        f"UUID=00000000-0000-0000-0000-000000000000 / ext4 defaults 0 1\n"
        f"{world.legacy_bind}\n{world.legacy_hub}\n",
        encoding="utf-8",
    )
    stub = world.bin / "hermes"
    stub.write_text(_HERMES_STUB, encoding="utf-8")
    stub.chmod(0o755)
    return world


def _run(
    world: _World,
    *,
    mounted: Sequence[Path] = (),
    times: int = 1,
) -> subprocess.CompletedProcess[str]:
    """가짜 세계에 프로비저너를 돌린다. 계정은 러너 자신 — 소유권 검증이 실제로 걸린다."""
    account = getpass.getuser()
    call = (
        f'AGENT_ACCOUNT="{account}" PEER_ACCOUNT="{account}" '
        f'AGENT_HOME="{world.agent_home}" PEER_HOME="{world.peer_home}" '
        f'STORE_ROOT="{world.root}/srv/autophagy-skills" FSTAB_PATH="{world.fstab}" '
        f'SKILL_ROOTS_ASSUME_ROOT=1 SKILL_ROOTS_SKIP_STORE=1 VERIFY_SKILL=wiki '
        f'bash "{_PROVISION}"\n'
    )
    script = (
        f'export SHIM_LOG="{world.log}"\n'
        f'export PATH="{world.bin}:$PATH"\n'
        f'export FAKE_MOUNTED="{":".join(str(path) for path in mounted)}"\n'
        + _SHIMS
        + call * times
    )
    return subprocess.run(
        ("bash", "-c", script), capture_output=True, text=True, check=False
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_provision_when_run_then_installs_no_read_only_bind(tmp_path: Path) -> None:
    # Given: 레거시 세계 — 두 bind 가 마운트돼 있고 fstab 에 그 두 줄이 있다.
    world = _world(tmp_path)
    script = _PROVISION.read_text(encoding="utf-8")

    # When
    result = _run(world, mounted=(world.hub_target, world.agent_skills))

    # Then: ro bind 를 만드는 코드 자체가 없다.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "mount --bind" not in script
    assert "mount -o remount" not in script
    # 그리고 레거시 두 줄은 **정확히 그 형태로** 인식돼 걷힌다.
    assert "none bind,ro,nosuid,nodev 0 0" in script
    assert "none bind,rw,nosuid,nodev,noexec 0 0" in script
    fstab = world.fstab.read_text(encoding="utf-8").splitlines()
    assert world.legacy_bind not in fstab
    assert world.legacy_hub not in fstab
    assert fstab[0].startswith("UUID=")  # 무관한 줄은 건드리지 않는다
    shim_log = world.log.read_text(encoding="utf-8").splitlines()
    # .hub 가 스킬 루트 안에 중첩이므로 반드시 먼저 풀려야 한다.
    umounts = [line.removeprefix("umount ") for line in shim_log if line.startswith("umount ")]
    assert umounts == [str(world.hub_target), str(world.agent_skills)]
    assert "systemctl daemon-reload" in shim_log


def test_provision_when_agent_root_is_created_then_it_is_agent_owned_0700(
    tmp_path: Path,
) -> None:
    # Given
    world = _world(tmp_path)
    account = getpass.getuser()

    # When
    result = _run(world)

    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert world.agent_skills.is_dir() and not world.agent_skills.is_symlink()
    assert world.agent_skills.stat().st_mode & 0o777 == 0o700
    assert (
        f"install -d -m 0700 -o {account} -g {account} {world.agent_skills}"
        in world.log.read_text(encoding="utf-8")
    )
    # root 소유 잔여 디렉터리는 지우지 않고 타임스탬프 백업으로 비켜둔다(노드 전용 경로).
    script = _PROVISION.read_text(encoding="utf-8")
    assert ".root-owned." in script
    assert 'mv -- "$AGENT_SKILLS_ROOT" "$backup"' in script


def test_provision_when_hub_state_exists_then_migrates_into_primary_root_nondestructively(
    tmp_path: Path,
) -> None:
    # Given
    world = _world(tmp_path)
    taps_digest = _digest(world.hub_state / "taps.json")

    # When: 두 번 돌려도 같은 결과여야 한다.
    result = _run(world, times=2)

    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert _digest(world.hub_target / "taps.json") == taps_digest
    assert (world.hub_target / "audit.log").read_text(encoding="utf-8") == "installed wiki\n"
    assert not (world.hub_state / "taps.json").exists()  # 복사가 아니라 이동
    assert world.hub_state.is_dir()  # 원본 디렉터리는 남긴다
    assert "taps.json readback OK" in result.stdout
    script = _PROVISION.read_text(encoding="utf-8")
    assert [line for line in script.splitlines() if "HUB_STATE" in line and "rm " in line] == []

def test_provision_when_the_hub_target_already_holds_a_newer_copy_then_the_rerun_keeps_it(
    tmp_path: Path,
) -> None:
    """이관 뒤 Hermes 가 `.hub/taps.json` 을 갱신했고 원본 자리에 옛 파일이 남아 있어도,
    재실행이 sha256 불일치로 죽어서는 안 된다 — readback 은 이번에 옮긴 것만 검사한다.
    """
    # Given
    world = _world(tmp_path)
    assert _run(world).returncode == 0
    (world.hub_target / "taps.json").write_text('{"taps": ["newer"]}\n', encoding="utf-8")
    (world.hub_state / "taps.json").write_text('{"taps": ["stale"]}\n', encoding="utf-8")

    # When
    result = _run(world)

    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert (world.hub_target / "taps.json").read_text(encoding="utf-8") == '{"taps": ["newer"]}\n'
    assert (world.hub_state / "taps.json").read_text(encoding="utf-8") == '{"taps": ["stale"]}\n'
    assert "hub migrate: taps.json already present" in result.stdout


def test_provision_when_config_has_no_skills_block_then_appends_canonical_block(
    tmp_path: Path,
) -> None:
    # Given: agent·peer 모두 `skills:` 가 없는 config.
    world = _world(tmp_path)

    # When: 두 번 돌려도 블록은 하나뿐이어야 한다.
    result = _run(world, times=2)

    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    agent_config = world.agent_config.read_text(encoding="utf-8")
    assert agent_config.count("\nskills:\n") == 1
    assert f"  external_dirs:\n    - {world.live}\n" in agent_config
    assert "  guard_agent_created: true\n" in agent_config
    assert agent_config.startswith(_BASE_CONFIG)  # 기존 내용은 그대로

    peer_config = world.peer_config.read_text(encoding="utf-8")
    assert peer_config.count("\nskills:\n") == 1
    assert "  guard_agent_created: true\n" in peer_config
    assert "external_dirs" not in peer_config  # peer 는 발견 목록을 받지 않는다

    backups = sorted(world.agent_config.parent.glob("config.yaml.bak-selfskill-*"))
    assert len(backups) == 1  # 쓰기 직전 1회만
    assert backups[0].read_text(encoding="utf-8") == _BASE_CONFIG


def test_provision_when_config_has_partial_skills_block_then_fails_closed(
    tmp_path: Path,
) -> None:
    # Given: 노드의 실제 상태 — `skills:` 는 있는데 필요한 키가 없다.
    world = _world(tmp_path)
    world.agent_config.write_text(_BASE_CONFIG + _PARTIAL_SKILLS, encoding="utf-8")
    before = world.agent_config.read_bytes()

    # When
    result = _run(world)

    # Then: 기존 블록을 넘겨짚어 고치지 않는다.
    assert result.returncode != 0
    assert world.agent_config.read_bytes() == before
    assert list(world.agent_config.parent.glob("config.yaml.bak-selfskill-*")) == []
    output = result.stdout + result.stderr
    assert "SKILLS-BLOCK-BLOCK" in output
    assert str(world.agent_config) in output
    assert "  external_dirs:" in output
    assert f"    - {world.live}" in output
    assert "  guard_agent_created: true" in output


def test_provision_when_owner_adds_the_missing_lines_then_the_rerun_converges(
    tmp_path: Path,
) -> None:
    # Given: 소유자가 안내받은 그대로 손으로 채워 넣은 config.
    world = _world(tmp_path)
    world.agent_config.write_text(
        _BASE_CONFIG
        + _PARTIAL_SKILLS
        + f"  external_dirs:\n    - {world.live}\n  guard_agent_created: true\n",
        encoding="utf-8",
    )
    before = world.agent_config.read_bytes()

    # When
    result = _run(world)

    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert world.agent_config.read_bytes() == before  # 이미 충족 → 무동작


def test_provision_when_peer_arm_runs_then_pins_both_live_self_skills(
    tmp_path: Path,
) -> None:
    # Given
    world = _world(tmp_path)

    # When: 두 번 돌려도 pin 은 각각 한 번뿐(멱등).
    result = _run(world, times=2)

    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    pins = (world.peer_skills / ".pins").read_text(encoding="utf-8").split()
    assert sorted(pins) == sorted(_PEER_PINS)
    for name in _PEER_PINS:
        assert f"peer pin: {name} pinned (readback OK)" in result.stdout


def test_provision_when_peer_residue_is_not_repo_authored_then_skips_loudly(
    tmp_path: Path,
) -> None:
    # Given: prompt 만 `author: autophagy-agents` 가 없다(노드 실측과 같은 모양).
    world = _world(tmp_path)

    # When
    result = _run(world)

    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (world.peer_skills / "coordination").exists()
    assert not (world.peer_skills / "wiki").exists()
    assert (world.peer_skills / "prompt" / "SKILL.md").is_file()
    assert (world.peer_skills / "apple" / "SKILL.md").is_file()  # 허용목록 밖은 손대지 않는다
    assert "PEER-RESIDUE-SKIP: prompt" in result.stdout


def test_repo_when_old_bootstrap_is_retired_then_no_references_remain() -> None:
    """실행 표면(`automation/**`·`tests/unit/**`)에 은퇴한 부트스트랩이 남으면 안 된다.

    범위 밖(보고 대상): `docs/**` 는 문서 과제가, `test_skill_deploy_boundary.py` 는 스킬
    배포 경계 과제가 소유하고, `.omo/plans/**` 는 완료된 웨이브의 이력 기록이다.
    """
    # Given
    sources = sorted(
        path
        for directory, patterns in ((_REPO / "automation", ("*.sh", "*.py")), (_REPO / "tests" / "unit", ("*.py",)))
        for pattern in patterns
        for path in directory.rglob(pattern)
        if "__pycache__" not in path.parts
    )

    # When
    offenders = [
        str(path.relative_to(_REPO))
        for path in sources
        if str(path.relative_to(_REPO)) not in _RETIREMENT_EXEMPT
        and "provision-readonly-skills" in path.read_text(encoding="utf-8", errors="surrogateescape")
    ]

    # Then
    assert not _RETIRED.exists()
    assert offenders == []
