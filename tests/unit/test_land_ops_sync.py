"""``land.sh`` makes push + runtime convergence one atomic, verified step.

The recurring drift is a two-step done as one: push to origin, then converge the
node — forget the second and prod runs stale code with nobody the wiser
(measured: the mirror 11 commits behind after another session's push).

DG-6 splits the contract by what the node's runtime actually IS:

* **release mode** (``/srv/autophagy-agent-current`` is a live symlink) — the
  resident mirror is no longer the runtime, only a drift observation post. A
  dirty or ahead mirror therefore WARNS instead of blocking a landing, and the
  hard post-condition moves onto the release: ``current`` must end at the sha we
  just pushed.
* **fallback mode** (``current`` absent — the documented one-command rollback)
  — every resolver falls back to the mirror, so the mirror IS the runtime again
  and the historical hard contract stands unchanged.

Hermetic: a throwaway dev checkout + bare origin + a fake NODE tree in
``tmp_path``; the dev-side git is real, the node side is a stubbed ``ssh`` that
rewrites ``/srv`` onto that tree, runs the command and journals argv. No node,
no network.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_LAND = _REPO / "automation" / "land.sh"
_PROBE = _REPO / "automation" / "checkout_mirror_probe.sh"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(repo), *args), check=True, capture_output=True, text=True
    )


def _identify(repo: Path, who: str) -> None:
    _git(repo, "config", "user.email", f"{who}@test.local")
    _git(repo, "config", "user.name", who)
    _git(repo, "config", "commit.gpgsign", "false")


def _dev_checkout(tmp_path: Path) -> tuple[Path, Path]:
    """A dev checkout with one unpushed commit, its bare origin, and a fake node tree.

    The node tree mirrors the real ``/srv`` layout, so ONE substitution in the
    ``ssh`` stub covers the mirror, the release store and the ``current`` symlink::

        node/autophagy-agents            the resident mirror checkout
        node/autophagy-agent-releases    the release store
        node/autophagy-agent-current     the release symlink (release mode only)

    ``current`` is deliberately absent by default: that is fallback mode, whose
    contract the historical tests below pin.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(
        ("git", "init", "--bare", "--initial-branch=main", str(origin)),
        check=True, capture_output=True, text=True,
    )
    dev = tmp_path / "dev"
    dev.mkdir()
    _git(dev, "init", "--initial-branch=main")
    _identify(dev, "dev")
    (dev / "automation").mkdir()
    (dev / "seed.txt").write_text("base\n", encoding="utf-8")
    _git(dev, "add", "seed.txt")
    _git(dev, "commit", "-m", "base")
    _git(dev, "remote", "add", "origin", str(origin))
    _git(dev, "push", "-u", "origin", "main")

    node = tmp_path / "node"
    node.mkdir()
    mirror = node / "autophagy-agents"
    subprocess.run(
        ("git", "clone", str(origin), str(mirror)), check=True, capture_output=True, text=True
    )
    _identify(mirror, "ops")

    # An unpushed dev commit: the happy path has something to land.
    (dev / "feature.txt").write_text("new\n", encoding="utf-8")
    _git(dev, "add", "feature.txt")
    _git(dev, "commit", "-m", "a feature to land")
    return dev, node


def _origin(tmp_path: Path) -> Path:
    return tmp_path / "origin.git"


def _mirror(node: Path) -> Path:
    return node / "autophagy-agents"


def _current(node: Path) -> Path:
    return node / "autophagy-agent-current"


def _release_at(node: Path, sha: str) -> Path:
    """Materialize a release tree carrying the real verifier and its sha marker."""
    release = node / "autophagy-agent-releases" / sha
    (release / "automation").mkdir(parents=True, exist_ok=True)
    (release / ".origin-sha").write_text(f"{sha}\n", encoding="utf-8")
    shutil.copy(_REPO / "automation" / "release_store.py", release / "automation")
    shutil.copy(_REPO / "automation" / "release_provenance.py", release / "automation")
    shutil.copy(_REPO / "automation" / "node_config.py", release / "automation")
    (release / "configs").mkdir()
    shutil.copy(_REPO / "configs" / "node.example.toml", release / "configs")
    return release


def _enable_release(tmp_path: Path, node: Path, sha: str, *, converge: str = "ok") -> None:
    """Put the node in release mode at ``sha`` with a stubbed converger.

    The converger lives ONLY inside the release tree — never in the mutable
    mirror — so a land that shelled out to the mirror copy fails loudly instead
    of quietly running a parallel session's uncommitted script.
    """
    release = _release_at(node, sha)
    journal = tmp_path / "argv.log"
    origin = tmp_path / "origin.git"
    flip = (
        'want="${RELEASE_EXPECTED_SHA:-}"\n'
        '[[ -n "$want" ]] || exit 64\n'
        f'rel="{node}/autophagy-agent-releases/$want"\n'
        'mkdir -p "$rel/automation"\n'
        'cp "$(dirname "$0")/release_store.py" "$rel/automation/" 2>/dev/null || true\n'
        'cp "$(dirname "$0")/release_provenance.py" "$rel/automation/" 2>/dev/null || true\n'
        'cp "$(dirname "$0")/node_config.py" "$rel/automation/" 2>/dev/null || true\n'
        'mkdir -p "$rel/configs"\n'
        'cp "$(dirname "$0")/../configs/node.example.toml" "$rel/configs/" 2>/dev/null || true\n'
        'printf "%s\\n" "$want" > "$rel/.origin-sha"\n'
        f'ln -sfn "$rel" "{node}/.current.tmp"\n'
        f'mv -T "{node}/.current.tmp" "{node}/autophagy-agent-current"\n'
    )
    race_dir = tmp_path / "race"
    race = (
        f'race="{race_dir}"; mkdir -p "$race"; git clone -q "{origin}" "$race/c"\n'
        'git -C "$race/c" config user.email race@test.local\n'
        'git -C "$race/c" config user.name race\n'
        'git -C "$race/c" config commit.gpgsign false\n'
        'echo raced > "$race/c/raced.txt"; git -C "$race/c" add raced.txt\n'
        'git -C "$race/c" commit -qm "another session landed first"\n'
        'git -C "$race/c" push -q origin main\n'
    )
    body = {
        "ok": flip,
        "stale": "",                    # succeeds but never advances `current`
        "fail": "exit 9\n",
        "race": race + flip,            # converges, but origin moved meanwhile
    }[converge]
    script = (
        "#!/usr/bin/env bash\nset -uo pipefail\n"
        f'printf "converge %s sha=%s\\n" "$0" "${{RELEASE_EXPECTED_SHA:-<none>}}" >> "{journal}"\n'
        f"{body}"
    )
    converger = release / "automation" / "converge-release-runtime.sh"
    converger.write_text(script, encoding="utf-8")
    converger.chmod(0o755)
    _current(node).symlink_to(release, target_is_directory=True)


def _stub_bin(
    tmp_path: Path, node: Path, *, ssh_down: bool = False, skip_pull: bool = False
) -> Path:
    stub = tmp_path / "stub"
    stub.mkdir(exist_ok=True)
    journal = tmp_path / "argv.log"
    if ssh_down:
        ssh_body = (
            "#!/usr/bin/env bash\n"
            f'printf "ssh %s\\n" "$*" >> "{journal}"\n'
            "exit 255\n"
        )
    else:
        ssh_body = (
            "#!/usr/bin/env bash\n"
            'set -uo pipefail\n'
            f'printf "ssh %s\\n" "$*" >> "{journal}"\n'
            'remote="${*: -1}"\n'
            f'remote="${{remote///srv/{node}}}"\n'
            'exec bash -c "$remote"\n'
        )
    (stub / "ssh").write_text(ssh_body, encoding="utf-8")
    # `sudo` sees the DECODED remote script, so it journals a readable first line
    # per node call. Journaling the ssh layer alone cannot discriminate: the
    # shipped drift guidance quotes `git pull --ff-only` in its recovery prose,
    # which would read as a pull that never happened.
    skip = 'case "$last" in "git -C "*" pull --ff-only") exit 0 ;; esac\n' if skip_pull else ""
    sudo = (
        "#!/usr/bin/env bash\nset -uo pipefail\n"
        "while [[ $# -gt 0 ]]; do case \"$1\" in -n|-H) shift ;; -u) shift 2 ;; *) break ;; esac; done\n"
        'last="${*: -1}"\n'
        'printf "run %s\\n" "${last%%$\'\\n\'*}" >> "' + str(journal) + '"\n'
        + skip +
        'exec "$@"\n'
    )
    (stub / "sudo").write_text(sudo, encoding="utf-8")
    for name in ("ssh", "sudo"):
        (stub / name).chmod(0o755)
    return stub


def _run_land(
    tmp_path: Path, dev: Path, node: Path, *, ssh_down: bool = False, skip_pull: bool = False
) -> subprocess.CompletedProcess[str]:
    shutil.copy(_LAND, dev / "automation" / "land.sh")
    (dev / "automation" / "land.sh").chmod(0o755)
    shutil.copy(_PROBE, dev / "automation")
    shutil.copy(_REPO / "automation" / "runtime_root.sh", dev / "automation")
    shutil.copy(_REPO / "automation" / "node_config.py", dev / "automation")
    shutil.copy(_REPO / "automation" / "node_config_sh.py", dev / "automation")
    (dev / "configs").mkdir(exist_ok=True)
    shutil.copy(_REPO / "configs" / "node.example.toml", dev / "configs")
    env = dict(os.environ)
    env["PATH"] = f"{_stub_bin(tmp_path, node, ssh_down=ssh_down, skip_pull=skip_pull)}{os.pathsep}{env['PATH']}"
    env["DEPLOY_SSH_HOST"] = "example-primary-node-not-this-host"
    return subprocess.run(
        ("bash", str(dev / "automation" / "land.sh")),
        cwd=str(dev), capture_output=True, text=True, check=False, env=env,
    )


def _argv(tmp_path: Path) -> str:
    log = tmp_path / "argv.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def _runs(tmp_path: Path) -> list[str]:
    """First line of every command land actually ran on the node."""
    return [line[4:] for line in _argv(tmp_path).splitlines() if line.startswith("run ")]


def _pulled(tmp_path: Path) -> bool:
    return any("pull --ff-only" in run for run in _runs(tmp_path))


def _never_invites_a_discard(stderr: str) -> bool:
    """A stranded or dirty checkout holds work that exists nowhere else.

    The shared guidance names the destructive commands only to forbid them, so
    the guard is not "the words are absent" but "they are never proposed".
    """
    return stderr.count("reset --hard") == stderr.count("Never git reset --hard")


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


# --- dev-side contract (mode-independent) -----------------------------------


def test_dirty_dev_checkout_refuses_before_push(tmp_path: Path) -> None:
    dev, node = _dev_checkout(tmp_path)
    (dev / "seed.txt").write_text("uncommitted edit\n", encoding="utf-8")
    result = _run_land(tmp_path, dev, node)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "LAND-BLOCK" in result.stderr
    assert "push" not in _argv(tmp_path)
    # The unpushed feature commit must still be only in dev.
    assert _git(_mirror(node), "log", "--oneline").stdout.count("a feature to land") == 0


def test_ssh_unreachable_after_successful_push_says_so(tmp_path: Path) -> None:
    dev, node = _dev_checkout(tmp_path)
    result = _run_land(tmp_path, dev, node, ssh_down=True)
    assert result.returncode != 0, result.stdout + result.stderr
    # The push (real git, no ssh) succeeded; the message must not imply otherwise.
    assert _head(dev) == _git(dev, "rev-parse", "origin/main").stdout.strip()
    combined = result.stdout + result.stderr
    assert "re-run" in combined and "land.sh" in combined

def test_a_non_main_head_never_publishes_local_main(tmp_path: Path) -> None:
    """``git push origin main`` publishes the local *main* ref, never HEAD.

    From any other branch that ref can carry another session's unpushed work. The
    push lands it on origin and only THEN does the ref check fail, announcing
    "the node is untouched" — so the operator reads a refusal and believes nothing
    happened, while origin has silently advanced onto work nobody reviewed.
    """
    dev, node = _dev_checkout(tmp_path)
    origin = _origin(tmp_path)
    before = _git(origin, "rev-parse", "refs/heads/main").stdout.strip()
    _git(dev, "checkout", "-b", "sidework")
    (dev / "side.txt").write_text("unrelated\n", encoding="utf-8")
    _git(dev, "add", "side.txt")
    _git(dev, "commit", "-m", "work on a side branch")

    result = _run_land(tmp_path, dev, node)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "LAND-BLOCK" in result.stderr
    # THE regression: origin must be byte-identical to before the refusal.
    assert _git(origin, "rev-parse", "refs/heads/main").stdout.strip() == before
    assert _argv(tmp_path) == ""


def test_detached_head_refuses_before_touching_the_node(tmp_path: Path) -> None:
    """A detached HEAD names no branch to land, so there is nothing to publish."""
    dev, node = _dev_checkout(tmp_path)
    _git(dev, "checkout", "--detach")

    result = _run_land(tmp_path, dev, node)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "LAND-BLOCK" in result.stderr
    assert "detached" in result.stderr
    assert _argv(tmp_path) == ""


# --- fallback mode: `current` absent, the mirror IS the runtime --------------


def test_fallback_mode_happy_path_leaves_the_mirror_at_origin_main(tmp_path: Path) -> None:
    dev, node = _dev_checkout(tmp_path)
    result = _run_land(tmp_path, dev, node)
    assert result.returncode == 0, result.stdout + result.stderr
    origin_main = _git(dev, "rev-parse", "origin/main").stdout.strip()
    assert _head(dev) == origin_main == _head(_mirror(node))


def test_fallback_mode_nothing_to_push_still_converges_the_mirror(tmp_path: Path) -> None:
    dev, node = _dev_checkout(tmp_path)
    _run_land(tmp_path, dev, node)  # first land pushes + syncs
    result = _run_land(tmp_path, dev, node)  # second land: nothing to push
    assert result.returncode == 0, result.stdout + result.stderr
    assert _pulled(tmp_path)  # the mirror is still verified and converged


def test_fallback_mode_mirror_not_at_origin_main_after_pull_fails(tmp_path: Path) -> None:
    dev, node = _dev_checkout(tmp_path)
    result = _run_land(tmp_path, dev, node, skip_pull=True)  # pull no-op ⇒ mirror stays stale
    assert result.returncode != 0, result.stdout + result.stderr
    assert "LAND-BLOCK" in result.stderr


def test_fallback_mode_ahead_mirror_refuses_with_non_destructive_guidance(
    tmp_path: Path,
) -> None:
    dev, node = _dev_checkout(tmp_path)
    mirror = _mirror(node)
    (mirror / "stranded.txt").write_text("only in the mirror\n", encoding="utf-8")
    _git(mirror, "add", "stranded.txt")
    _git(mirror, "commit", "-m", "a commit only the mirror has")
    result = _run_land(tmp_path, dev, node)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "format-patch" in result.stderr
    assert _never_invites_a_discard(result.stderr)


def test_fallback_mode_dirty_mirror_still_hard_blocks(tmp_path: Path) -> None:
    # The mirror is the runtime here, so its uncommitted edits are running in
    # prod and its ff-pull is jammed: the pre-DG-6 hard contract must survive.
    dev, node = _dev_checkout(tmp_path)
    (_mirror(node) / "seed.txt").write_text("edited on the node\n", encoding="utf-8")
    result = _run_land(tmp_path, dev, node)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "LAND-BLOCK" in result.stderr
    assert not _pulled(tmp_path)


# --- release mode: `current` is live, the mirror is only an observation post --


def test_release_mode_dirty_mirror_no_longer_blocks_the_landing(tmp_path: Path) -> None:
    dev, node = _dev_checkout(tmp_path)
    _enable_release(tmp_path, node, "0" * 40)
    (_mirror(node) / "seed.txt").write_text("a parallel session's edit\n", encoding="utf-8")
    result = _run_land(tmp_path, dev, node)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "LAND-MIRROR-WARN" in result.stderr
    assert "LAND-BLOCK" not in result.stderr
    # A dirty mirror cannot ff-pull; attempting it would only manufacture noise.
    assert not _pulled(tmp_path)
    assert _current(node).resolve().name == _head(dev)


def test_release_mode_ahead_mirror_is_still_surfaced(tmp_path: Path) -> None:
    dev, node = _dev_checkout(tmp_path)
    _enable_release(tmp_path, node, "0" * 40)
    mirror = _mirror(node)
    (mirror / "stranded.txt").write_text("only in the mirror\n", encoding="utf-8")
    _git(mirror, "add", "stranded.txt")
    _git(mirror, "commit", "-m", "a commit only the mirror has")
    result = _run_land(tmp_path, dev, node)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "LAND-MIRROR-WARN" in result.stderr
    # Downgraded to a warning, never to silence: the stranded commit exists
    # nowhere else, so the recovery text must still arrive intact.
    assert "format-patch" in result.stderr
    assert _never_invites_a_discard(result.stderr)


def test_release_mode_ff_pull_is_best_effort_when_the_mirror_is_clean_and_behind(
    tmp_path: Path,
) -> None:
    dev, node = _dev_checkout(tmp_path)
    _enable_release(tmp_path, node, "0" * 40)
    result = _run_land(tmp_path, dev, node, skip_pull=True)
    # The pull was attempted (clean + behind) but neutered; the landing stands
    # because the mirror no longer decides what production runs.
    assert result.returncode == 0, result.stdout + result.stderr
    assert _pulled(tmp_path)
    assert "LAND-BLOCK" not in result.stderr
    assert _current(node).resolve().name == _head(dev)


def test_release_mode_converges_the_runtime_to_the_pushed_sha(tmp_path: Path) -> None:
    dev, node = _dev_checkout(tmp_path)
    _enable_release(tmp_path, node, "0" * 40)
    result = _run_land(tmp_path, dev, node)
    assert result.returncode == 0, result.stdout + result.stderr
    dev_head = _head(dev)
    # The runtime advanced to exactly what we pushed...
    assert _current(node).resolve().name == dev_head
    assert (_current(node) / ".origin-sha").read_text(encoding="utf-8").strip() == dev_head
    # ...driven by an explicitly pinned sha, not by whatever origin happened to
    # be when the converger re-read it.
    assert f"sha={dev_head}" in _argv(tmp_path)


def test_release_mode_runs_the_converger_from_the_release_not_the_mirror(
    tmp_path: Path,
) -> None:
    # Downgrading the mirror to a warning is only sound if we stop EXECUTING its
    # shell: a dirty mirror's converger would be a parallel session's uncommitted
    # script running as ops.
    dev, node = _dev_checkout(tmp_path)
    _enable_release(tmp_path, node, "0" * 40)
    _run_land(tmp_path, dev, node)
    converge_lines = [line for line in _argv(tmp_path).splitlines() if line.startswith("converge ")]
    assert converge_lines, _argv(tmp_path)
    assert all("autophagy-agent-current" in line for line in converge_lines), converge_lines
    assert not any("autophagy-agents/" in line for line in converge_lines), converge_lines


def test_release_mode_stale_current_after_converge_is_a_stranded_half_landing(
    tmp_path: Path,
) -> None:
    dev, node = _dev_checkout(tmp_path)
    _enable_release(tmp_path, node, "0" * 40, converge="stale")
    result = _run_land(tmp_path, dev, node)
    assert result.returncode != 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "re-run" in combined and "land.sh" in combined
    # The push is real and must never be described as lost.
    assert _head(dev) == _git(dev, "rev-parse", "origin/main").stdout.strip()


def test_release_mode_converge_failure_is_reported_not_swallowed(tmp_path: Path) -> None:
    dev, node = _dev_checkout(tmp_path)
    _enable_release(tmp_path, node, "0" * 40, converge="fail")
    result = _run_land(tmp_path, dev, node)
    assert result.returncode != 0, result.stdout + result.stderr
    # A failed install/flip leaves production on the previous release, so it is a
    # stranded half-landing — not a mirror warning, and never a silent success.
    assert "install/flip failed" in result.stderr
    assert "re-run" in result.stderr


def test_release_mode_rechecks_origin_after_the_converge(tmp_path: Path) -> None:
    # The snapshot pins origin BEFORE running its command; another session can
    # land in the window, leaving `current` at a sha origin/main no longer has.
    dev, node = _dev_checkout(tmp_path)
    _enable_release(tmp_path, node, "0" * 40, converge="race")
    result = _run_land(tmp_path, dev, node)
    assert result.returncode != 0, result.stdout + result.stderr


def test_corrupt_current_is_blocked_never_treated_as_absent(tmp_path: Path) -> None:
    # `[[ -e ]]` is false for a dangling symlink, so a naive resolver would call
    # this "absent" and silently demote a release node to the mirror.
    dev, node = _dev_checkout(tmp_path)
    _current(node).symlink_to(node / "autophagy-agent-releases" / "gone")
    result = _run_land(tmp_path, dev, node)
    assert result.returncode != 0, result.stdout + result.stderr
    assert not _pulled(tmp_path)


def test_current_that_is_not_a_symlink_is_blocked(tmp_path: Path) -> None:
    dev, node = _dev_checkout(tmp_path)
    _current(node).mkdir(parents=True)
    result = _run_land(tmp_path, dev, node)
    assert result.returncode != 0, result.stdout + result.stderr
    assert not _pulled(tmp_path)


# --- structural contracts ---------------------------------------------------


def test_land_shares_the_mirror_verdict_with_healthcheck() -> None:
    # One rule, two consumers: healthcheck DETECTS drift, land CONVERGES around
    # it. Re-implementing the verdict here is how the two would disagree.
    script = _LAND.read_text(encoding="utf-8")
    assert "checkout_mirror_probe.sh" in script
    assert "checkout_mirror_verdict" in script
    # Shipped by value to the node: the probe judges a checkout that lives there,
    # and sourcing the node's own copy would execute the very mirror we no
    # longer trust.
    assert "declare -f" in script


def test_runtime_root_is_resolved_node_side_not_on_the_workstation() -> None:
    # A workstation-side resolver would stat /srv/autophagy-agent-current on the
    # WRONG host and pick fallback mode for a release node (deploy-skill.sh:52
    # precedent).
    script = _LAND.read_text(encoding="utf-8")
    assert "autophagy_runtime_root" not in script
    abi_line = next(line for line in script.splitlines() if "skill_library_abi.py" in line)
    assert "/srv/autophagy-agents/automation/skill_library_abi.py" not in abi_line
    assert "/srv/autophagy-agent-current/automation/skill_library_abi.py" not in abi_line
    # ...and each mode hands the probe the root that mode actually runs from.
    assert 'land_abi_probe "$RELEASE_CURRENT"' in script
    assert 'land_abi_probe "$MIRROR_CHECKOUT"' in script


def test_the_old_mirror_head_equality_post_condition_is_gone() -> None:
    # DG-6 replaces "the mirror must equal origin/main" with "the RUNTIME must
    # be at the sha we pushed" — unconditional mirror equality is exactly what
    # a parallel session's dirty checkout used to veto.
    script = _LAND.read_text(encoding="utf-8")
    assert "OPS_CHECKOUT" not in script
    assert "MIRROR_CHECKOUT" in script
    assert "land_mirror_warn" in script
    assert "RELEASE_EXPECTED_SHA" in script
