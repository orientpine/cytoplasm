"""The ops deploy checkout must refuse every commit attempted inside it.

``/srv/autophagy-agents`` is a one-way mirror of origin/main: the only writes allowed
there are ``git fetch`` and ``git pull --ff-only`` (root AGENTS.md, "ops 체크아웃 단방향
규칙"). Agents and humans committed inside it anyway, so prod ran code that never reached
origin/main and was silently reverted by the next clean-checkout deploy, while the
divergence blocked every session's ff-pull. ``probe_checkout_mirrors_origin`` DETECTS that
state after the fact; the pre-commit hook installed by ``bootstrap-accounts.sh`` REFUSES it
up front.

Hermetic: the installer is sourced from a derived copy of bootstrap-accounts.sh cut just
before its root-only main body, ownership flags are stripped by a shell shim, and every git
operation runs against throwaway repositories under ``tmp_path``. This repository's own
``.git/hooks`` is never read or written.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_BOOTSTRAP = _REPO / "automation" / "bootstrap-accounts.sh"
_MAIN_START = 'ROLE="${1:-}"'
_INSTALLER = "install_commit_refusal_hook"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(repo), *args), check=True, capture_output=True, text=True
    )


def _try_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(repo), *args), check=False, capture_output=True, text=True
    )


def _identity(repo: Path) -> None:
    _ = _git(repo, "config", "user.email", "ops@test.local")
    _ = _git(repo, "config", "user.name", "ops")
    _ = _git(repo, "config", "commit.gpgsign", "false")


def _deploy_checkout(tmp_path: Path) -> tuple[Path, Path]:
    """A deploy checkout in its healthy shape, plus the bare origin it mirrors."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _ = subprocess.run(
        ("git", "init", "--bare", "--initial-branch=main", str(origin)),
        check=True,
        capture_output=True,
        text=True,
    )
    checkout = tmp_path / "autophagy-agents"
    checkout.mkdir()
    _ = _git(checkout, "init", "--initial-branch=main")
    _identity(checkout)
    (checkout / "SKILL.md").write_text("version: 1.5.3\n", encoding="utf-8")
    _ = _git(checkout, "add", "SKILL.md")
    _ = _git(checkout, "commit", "-m", "deployed state")
    _ = _git(checkout, "remote", "add", "origin", str(origin))
    _ = _git(checkout, "push", "-u", "origin", "main")
    return checkout, origin


def _sourceable(tmp_path: Path) -> Path:
    """bootstrap-accounts.sh minus its main body - sourcing must not provision a node."""
    lines = _BOOTSTRAP.read_text(encoding="utf-8").splitlines()
    starts = [index for index, line in enumerate(lines) if line.strip() == _MAIN_START]
    assert len(starts) == 1, f"expected exactly one `{_MAIN_START}` line, got {len(starts)}"
    sourceable = tmp_path / "bootstrap_sourceable.sh"
    body = "\n".join(lines[: starts[0]]) + "\n"
    body = body.replace(
        'readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"',
        f'readonly REPO_ROOT="{_REPO}"',
    )
    sourceable.write_text(body, encoding="utf-8")
    return sourceable


def _install_hook(
    tmp_path: Path, checkout: Path, *, times: int = 1
) -> subprocess.CompletedProcess[str]:
    """Run the real installer against a throwaway checkout, minus the ops:autophagy chown."""
    shim = (
        "install() {\n"
        "  local -a args=()\n"
        "  while (( $# > 0 )); do\n"
        '    case "$1" in\n'
        "      -o|-g) shift 2 ;;\n"
        '      *) args+=("$1"); shift ;;\n'
        "    esac\n"
        "  done\n"
        '  command install "${args[@]}"\n'
        "}\n"
    )
    calls = "".join(f'{_INSTALLER} "{checkout}"\n' for _ in range(times))
    script = f'source "{_sourceable(tmp_path)}"\n{shim}{calls}'
    return subprocess.run(
        ("bash", "-c", script), capture_output=True, text=True, check=False
    )


def _installed_hooks(checkout: Path) -> list[Path]:
    hooks = checkout / ".git" / "hooks"
    return sorted(p for p in hooks.glob("pre-commit*") if p.suffix != ".sample")


def _attempt_commit(checkout: Path) -> subprocess.CompletedProcess[str]:
    (checkout / "SKILL.md").write_text("version: 1.5.5\n", encoding="utf-8")
    _ = _git(checkout, "add", "SKILL.md")
    return _try_git(checkout, "commit", "-m", "learned in prod, never pushed")


def test_a_commit_inside_the_deploy_checkout_is_refused(tmp_path: Path) -> None:
    # Given the deploy checkout with the refusal hook installed
    checkout, _ = _deploy_checkout(tmp_path)
    install = _install_hook(tmp_path, checkout)
    assert install.returncode == 0, install.stdout + install.stderr
    before = _git(checkout, "rev-parse", "HEAD").stdout.strip()

    # When someone commits the way the 2026-07-27 incident did
    result = _attempt_commit(checkout)

    # Then the commit never happens and HEAD is untouched
    assert result.returncode != 0, result.stdout + result.stderr
    assert _git(checkout, "rev-parse", "HEAD").stdout.strip() == before


def test_the_refused_commit_keeps_the_work_staged(tmp_path: Path) -> None:
    """Refusal must not be destructive - the edit that was blocked still exists."""
    checkout, _ = _deploy_checkout(tmp_path)
    _ = _install_hook(tmp_path, checkout)

    _ = _attempt_commit(checkout)

    assert (checkout / "SKILL.md").read_text(encoding="utf-8") == "version: 1.5.5\n"
    staged = _git(checkout, "diff", "--cached", "--name-only").stdout.split()
    assert staged == ["SKILL.md"]


def test_amending_the_deployed_commit_is_refused(tmp_path: Path) -> None:
    """--amend rewrites history in place, which is the same one-way-mirror violation."""
    checkout, _ = _deploy_checkout(tmp_path)
    _ = _install_hook(tmp_path, checkout)
    before = _git(checkout, "rev-parse", "HEAD").stdout.strip()

    result = _try_git(checkout, "commit", "--amend", "-m", "reworded in prod")

    assert result.returncode != 0, result.stdout + result.stderr
    assert _git(checkout, "rev-parse", "HEAD").stdout.strip() == before


def test_the_refusal_tells_the_operator_where_to_commit_instead(tmp_path: Path) -> None:
    checkout, _ = _deploy_checkout(tmp_path)
    _ = _install_hook(tmp_path, checkout)

    result = _attempt_commit(checkout)

    message = result.stdout + result.stderr
    assert "origin/main" in message
    assert "git pull --ff-only" in message


def test_installing_twice_leaves_exactly_one_hook(tmp_path: Path) -> None:
    """Idempotency: bootstrap-accounts.sh is re-run on every node provisioning pass."""
    checkout, _ = _deploy_checkout(tmp_path)

    once = _install_hook(tmp_path, checkout)
    assert once.returncode == 0, once.stdout + once.stderr
    first = [(p.name, p.read_bytes()) for p in _installed_hooks(checkout)]

    twice = _install_hook(tmp_path, checkout, times=2)
    assert twice.returncode == 0, twice.stdout + twice.stderr

    assert [(p.name, p.read_bytes()) for p in _installed_hooks(checkout)] == first
    assert len(first) == 1


def test_the_installed_hook_is_executable(tmp_path: Path) -> None:
    checkout, _ = _deploy_checkout(tmp_path)
    _ = _install_hook(tmp_path, checkout)

    hooks = _installed_hooks(checkout)
    assert [p.name for p in hooks] == ["pre-commit"]
    assert hooks[0].stat().st_mode & 0o111 == 0o111


def test_pull_ff_only_still_moves_the_checkout(tmp_path: Path) -> None:
    """The one write the checkout is allowed to take must survive the hook."""
    checkout, origin = _deploy_checkout(tmp_path)
    _ = _install_hook(tmp_path, checkout)

    workstation = tmp_path / "workstation"
    _ = subprocess.run(
        ("git", "clone", str(origin), str(workstation)),
        check=True,
        capture_output=True,
        text=True,
    )
    _identity(workstation)
    (workstation / "SKILL.md").write_text("version: 1.5.5\n", encoding="utf-8")
    _ = _git(workstation, "commit", "-am", "pushed through origin/main")
    _ = _git(workstation, "push", "origin", "main")
    pushed = _git(workstation, "rev-parse", "HEAD").stdout.strip()

    pull = _try_git(checkout, "pull", "--ff-only")

    assert pull.returncode == 0, pull.stdout + pull.stderr
    assert _git(checkout, "rev-parse", "HEAD").stdout.strip() == pushed


def test_bootstrap_installs_the_refusal_hook_on_the_deploy_dir() -> None:
    """Registry check: the provisioning flow must actually call the installer."""
    script = _BOOTSTRAP.read_text(encoding="utf-8")
    body = script.split(_MAIN_START, 1)[1]

    assert f'{_INSTALLER} "$DEPLOY_DIR"' in body
