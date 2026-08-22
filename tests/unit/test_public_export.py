from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).resolve().parents[2] / "automation" / "public_export.sh"
_VERSION = "v1.2.3"
_CANARY_DIR = ".public-export-scanner-canary"

#: Stand-in gitleaks. It records its argument list, refuses an absolute scan
#: target (absolute finding paths break the repo's relative allowlist - see the
#: P0-3 learnings), and emulates --report-path by reporting whichever canary
#: files the real scanner would have found under the current directory.
#: GITLEAKS_BLIND_CANARY=1 makes it report nothing so the canary guard can be
#: exercised RED.
_GITLEAKS_STUB = f"""#!/bin/sh
printf "%s\\n" "$*" >> "$GITLEAKS_LOG"
report=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "--report-path" ]; then
    report="$arg"
  else
    case "$arg" in /*) exit 31 ;; esac
  fi
  prev="$arg"
done
if [ -n "$report" ]; then
  printf '[' > "$report"
  sep=""
  if [ "${{GITLEAKS_BLIND_CANARY:-0}}" != "1" ]; then
    for name in plain.txt ignored.txt; do
      if [ -f "{_CANARY_DIR}/$name" ]; then
        printf '%s{{"File":"%s"}}' "$sep" "{_CANARY_DIR}/$name" >> "$report"
        sep=","
      fi
    done
  fi
  printf ']\\n' >> "$report"
fi
exit "${{GITLEAKS_TEST_EXIT:-0}}"
"""


@dataclass(frozen=True, slots=True)
class ExportSandbox:
    source: Path
    public_remote: Path
    target: Path
    signing_key: Path
    environment: dict[str, str]
    gitleaks_log: Path
    pytest_log: Path


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write_executable(path: Path, body: str) -> None:
    _ = path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _run(
    sandbox: ExportSandbox,
    *extra: str,
    version: str | None = _VERSION,
) -> subprocess.CompletedProcess[str]:
    version_arguments = () if version is None else ("--version", version)
    return subprocess.run(
        (
            "bash",
            str(_SCRIPT),
            "--source-repo",
            str(sandbox.source),
            "--source-ref",
            "origin/main",
            "--target-dir",
            str(sandbox.target),
            "--remote",
            str(sandbox.public_remote),
            *version_arguments,
            "--signing-key",
            str(sandbox.signing_key),
            "--repository-name",
            "local/autophagy-public",
            "--visibility",
            "public",
            *extra,
        ),
        check=False,
        capture_output=True,
        text=True,
        env=sandbox.environment,
    )


def _commit_source(sandbox: ExportSandbox, message: str) -> None:
    _ = _git(sandbox.source, "add", "-A")
    _ = _git(sandbox.source, "commit", "-m", message)
    _ = _git(sandbox.source, "push", "origin", "main")


def _remote_has_ref(remote: Path, reference: str) -> bool:
    result = subprocess.run(
        ("git", "-C", str(remote), "show-ref", "--verify", "--quiet", reference),
        check=False,
    )
    return result.returncode == 0


@pytest.fixture
def export_sandbox(tmp_path: Path) -> ExportSandbox:
    private_remote = tmp_path / "private.git"
    public_remote = tmp_path / "public.git"
    _ = _git(tmp_path, "init", "--bare", "--initial-branch=main", str(private_remote))
    _ = _git(tmp_path, "init", "--bare", "--initial-branch=main", str(public_remote))

    source = tmp_path / "source"
    _ = _git(tmp_path, "clone", str(private_remote), str(source))
    _ = _git(source, "config", "user.name", "Test Maintainer")
    _ = _git(source, "config", "user.email", "maintainer@example.invalid")
    (source / "configs").mkdir()
    _ = (source / "configs" / "public-export-manifest.txt").write_text(
        ".omo/\ndocs/qa/\ndocs/guide/private-runbook.md\n",
        encoding="utf-8",
    )
    (source / ".omo").mkdir()
    _ = (source / ".omo" / "private.md").write_text("private plan\n", encoding="utf-8")
    (source / "docs" / "qa").mkdir(parents=True)
    _ = (source / "docs" / "qa" / "evidence.md").write_text(
        "private evidence\n", encoding="utf-8"
    )
    (source / "docs" / "guide").mkdir()
    _ = (source / "docs" / "guide" / "private-runbook.md").write_text(
        "private operations\n", encoding="utf-8"
    )
    (source / "tests" / "unit").mkdir(parents=True)
    _ = (source / "tests" / "unit" / "test_smoke.py").write_text(
        "def test_smoke() -> None:\n    assert True\n", encoding="utf-8"
    )
    _ = (source / "README.md").write_text("public content\n", encoding="utf-8")
    (source / "automation").mkdir()
    _ = (source / "automation" / "public_export_redaction.py").write_bytes(
        (_SCRIPT.parent / "public_export_redaction.py").read_bytes()
    )
    vendor_mailon = source / "skills" / "mail" / "vendor" / "mailon"
    vendor_tests = source / "skills" / "mail" / "vendor" / "tests"
    vendor_mailon.mkdir(parents=True)
    vendor_tests.mkdir(parents=True)
    _ = (vendor_mailon / "resolve.py").write_text(
        '#   대표 도메인 "김샘플" <k@example.invalid> :: 예시연구원 - 예시센터\n',
        encoding="utf-8",
    )
    _ = (vendor_mailon.parent / "requirements.txt").write_text(
        "# Versions locked to the set validated on prod (example123, CPython 3.13)\n",
        encoding="utf-8",
    )
    offline_fixture = "\n".join(
        (
            'ACCOUNT = "person@example-lab.re.kr"',
            'me = f"\\"홍길동\\" <{ACCOUNT}>"',
            'org = "예시연구원 - 예시센터"',
            "",
        )
    )
    _ = (vendor_tests / "test_offline.py").write_text(offline_fixture, encoding="utf-8")
    _ = _git(source, "add", "-A")
    _ = _git(source, "commit", "-m", "seed private source")
    _ = _git(source, "push", "-u", "origin", "main")

    signing_key = tmp_path / "update-trust"
    _ = subprocess.run(
        ("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", str(signing_key)),
        check=True,
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    gitleaks_log = tmp_path / "gitleaks.log"
    pytest_log = tmp_path / "pytest.log"
    _write_executable(fake_bin / "gitleaks", _GITLEAKS_STUB)
    _write_executable(
        fake_bin / "pytest",
        "#!/bin/sh\nexit 41\n",
    )
    python_stub = "\n".join(
        (
            '#!/bin/sh\nif [ "$1" = "-m" ] && [ "$2" = "pytest" ]; then',
            '  GIT_MASTER=1 git ls-files --error-unmatch README.md >/dev/null 2>&1 || exit 42',
            '  printf "%s:%s\\n" "$PWD" "$*" >> "$PYTEST_LOG"',
            '  exit "${PYTEST_TEST_EXIT:-0}"\nfi\nexec /usr/bin/python3 "$@"',
            "",
        )
    )
    _write_executable(fake_bin / "python3", python_stub)
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "GITLEAKS_LOG": str(gitleaks_log),
            "PYTEST_LOG": str(pytest_log),
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return ExportSandbox(
        source=source,
        public_remote=public_remote,
        target=tmp_path / "export",
        signing_key=signing_key,
        environment=environment,
        gitleaks_log=gitleaks_log,
        pytest_log=pytest_log,
    )


def test_export_creates_fresh_history_and_pushes_verified_tag(
    export_sandbox: ExportSandbox,
    tmp_path: Path,
) -> None:
    # Given a clean private source, local public stand-in, and update-trust key.
    # When one export run snapshots, validates, signs, and pushes the release.
    result = _run(export_sandbox)

    # Then the public tree has one root, no private paths, and a verifiable SSH tag.
    assert result.returncode == 0, result.stderr
    assert _git(export_sandbox.target, "rev-list", "--count", "HEAD") == "1"
    assert not (export_sandbox.target / ".omo").exists()
    assert not (export_sandbox.target / "docs" / "qa").exists()
    assert not (export_sandbox.target / "docs" / "guide" / "private-runbook.md").exists()
    assert (export_sandbox.target / "README.md").is_file()
    exported_vendor = export_sandbox.target / "skills" / "mail" / "vendor"
    assert "<example-organization>" in (
        exported_vendor / "mailon" / "resolve.py"
    ).read_text(encoding="utf-8")
    assert "<primary-node>" in (exported_vendor / "requirements.txt").read_text(
        encoding="utf-8"
    )
    assert "person@example.invalid" in (
        exported_vendor / "tests" / "test_offline.py"
    ).read_text(encoding="utf-8")
    assert _remote_has_ref(export_sandbox.public_remote, "refs/heads/main")
    assert _remote_has_ref(export_sandbox.public_remote, f"refs/tags/{_VERSION}")

    allowed_signers = tmp_path / "allowed-signers"
    public_key = export_sandbox.signing_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    _ = allowed_signers.write_text(
        f'update-trust@autophagy namespaces="git" {public_key}\n', encoding="utf-8"
    )
    verified = subprocess.run(
        (
            "git",
            "-C",
            str(export_sandbox.public_remote),
            "-c",
            "gpg.format=ssh",
            "-c",
            f"gpg.ssh.allowedSignersFile={allowed_signers}",
            "verify-tag",
            _VERSION,
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stderr
    assert "tests/unit" in export_sandbox.pytest_log.read_text(encoding="utf-8")
    scans = export_sandbox.gitleaks_log.read_text(encoding="utf-8")
    assert "git" in scans and "dir" in scans


def test_export_refuses_dirty_source_before_creating_target(
    export_sandbox: ExportSandbox,
) -> None:
    # Given an untracked source file.
    _ = (export_sandbox.source / "scratch.txt").write_text(
        "unfinished\n", encoding="utf-8"
    )
    # When export is attempted.
    result = _run(export_sandbox)
    # Then it fails before creating local or remote release state.
    assert result.returncode != 0
    assert not export_sandbox.target.exists()
    assert not _remote_has_ref(export_sandbox.public_remote, "refs/heads/main")


def test_export_derives_version_from_the_release_tag_on_source_commit(
    export_sandbox: ExportSandbox,
) -> None:
    # Given: land already attached the private release tag to origin/main.
    _ = _git(export_sandbox.source, "tag", "-a", _VERSION, "-m", f"release {_VERSION}")

    # When: the maintainer omits the public cut version.
    result = _run(export_sandbox, version=None)

    # Then: the public release reuses the source commit's version.
    assert result.returncode == 0, result.stderr
    assert _remote_has_ref(export_sandbox.public_remote, f"refs/tags/{_VERSION}")


def test_explicit_version_wins_over_the_source_commit_tag(
    export_sandbox: ExportSandbox,
) -> None:
    # Given: the source commit carries a different private-channel version.
    derived = "v9.8.7"
    _ = _git(export_sandbox.source, "tag", "-a", derived, "-m", f"release {derived}")

    # When: the maintainer explicitly chooses the public cut version.
    result = _run(export_sandbox)

    # Then: the explicit value wins and no derived tag reaches the public remote.
    assert result.returncode == 0, result.stderr
    assert _remote_has_ref(export_sandbox.public_remote, f"refs/tags/{_VERSION}")
    assert not _remote_has_ref(export_sandbox.public_remote, f"refs/tags/{derived}")


def test_export_without_version_refuses_a_source_commit_without_release_tag(
    export_sandbox: ExportSandbox,
) -> None:
    # Given: origin/main has no semantic release tag.
    # When: the maintainer also omits --version.
    result = _run(export_sandbox, version=None)

    # Then: derivation fails closed before any public state is created.
    assert result.returncode != 0
    assert "source commit has no semantic release tag" in result.stderr
    assert not export_sandbox.target.exists()
    assert not _remote_has_ref(export_sandbox.public_remote, "refs/heads/main")


def test_export_reports_when_snapshot_redaction_helper_is_absent(
    export_sandbox: ExportSandbox,
) -> None:
    # Given: the source snapshot lacks the helper it must execute from that snapshot.
    helper = export_sandbox.source / "automation" / "public_export_redaction.py"
    helper.unlink()
    _commit_source(export_sandbox, "remove snapshot redaction helper")

    # When: export reaches snapshot materialisation.
    result = _run(export_sandbox)

    # Then: the missing self-copy dependency is named directly.
    assert result.returncode != 0
    assert "snapshot redaction helper is absent" in result.stderr
    assert not export_sandbox.target.exists()
    assert not _remote_has_ref(export_sandbox.public_remote, "refs/heads/main")


def test_export_refuses_manifest_without_mandatory_private_paths(
    export_sandbox: ExportSandbox,
) -> None:
    # Given a tracked manifest that omits docs/qa/.
    manifest = export_sandbox.source / "configs" / "public-export-manifest.txt"
    _ = manifest.write_text(".omo/\ndocs/guide/private-runbook.md\n", encoding="utf-8")
    _commit_source(export_sandbox, "break mandatory exclusions")
    # When export is attempted.
    result = _run(export_sandbox)
    # Then the fail-closed parser refuses to publish anything.
    assert result.returncode != 0
    assert not export_sandbox.target.exists()
    assert not _remote_has_ref(export_sandbox.public_remote, "refs/heads/main")


def test_export_refuses_missing_update_trust_key(
    export_sandbox: ExportSandbox,
) -> None:
    # Given a missing producer key instead of the update-trust private key.
    missing = export_sandbox.signing_key.parent / "missing-key"
    # When export is attempted with that key path.
    result = _run(export_sandbox, "--signing-key", str(missing))
    # Then no unsigned export is retained or pushed.
    assert result.returncode != 0
    assert not export_sandbox.target.exists()
    assert not _remote_has_ref(export_sandbox.public_remote, "refs/heads/main")
    assert not _remote_has_ref(export_sandbox.public_remote, f"refs/tags/{_VERSION}")


def test_atomic_push_keeps_branch_absent_when_tag_is_rejected(
    export_sandbox: ExportSandbox,
) -> None:
    # Given a stand-in remote that accepts branches but rejects tag updates.
    update_hook = export_sandbox.public_remote / "hooks" / "update"
    _write_executable(
        update_hook,
        '#!/bin/sh\ncase "$1" in refs/tags/*) exit 1 ;; *) exit 0 ;; esac\n',
    )
    # When the combined branch-and-tag push is attempted.
    result = _run(export_sandbox)
    # Then atomic push failure leaves neither half of the release published.
    assert result.returncode != 0
    assert not _remote_has_ref(export_sandbox.public_remote, "refs/heads/main")
    assert not _remote_has_ref(export_sandbox.public_remote, f"refs/tags/{_VERSION}")


@pytest.mark.parametrize(
    "hostile_remote",
    (
        "--upload-pack=touch /tmp/public-export-c2",
        "-oProxyCommand=touch /tmp/public-export-c2",
        "ext::sh -c touch% /tmp/public-export-c2",
        "EXT::sh -c touch% /tmp/public-export-c2",
    ),
)
def test_export_refuses_option_shaped_and_command_executing_remotes(
    export_sandbox: ExportSandbox,
    hostile_remote: str,
) -> None:
    # Given a --remote value that git would read as an option or as a transport
    # that executes a command rather than as a repository location.
    # When export is attempted with it.
    result = _run(export_sandbox, "--remote", hostile_remote)
    # Then it is refused before any git invocation receives the value.
    assert result.returncode != 0
    assert not export_sandbox.target.exists()
    assert not Path("/tmp/public-export-c2").exists()


def test_export_refuses_a_signing_key_path_that_injects_a_git_config_line(
    export_sandbox: ExportSandbox,
) -> None:
    # Given a signing-key path carrying a newline, which would append a second
    # line to `git -c "user.signingkey=..."`.
    injected = f"{export_sandbox.signing_key}\nuser.email=attacker@example.invalid"
    # When export is attempted with that path.
    result = _run(export_sandbox, "--signing-key", injected)
    # Then the control character is refused before any git config is assembled.
    assert result.returncode != 0
    assert not export_sandbox.target.exists()
    assert not _remote_has_ref(export_sandbox.public_remote, f"refs/tags/{_VERSION}")


def test_export_refuses_a_scanner_that_misses_the_planted_canary(
    export_sandbox: ExportSandbox,
) -> None:
    # Given a gitleaks that exits 0 while reporting nothing at all - the silent
    # zero-finding failure mode a version-dependent scan skip would produce.
    export_sandbox.environment["GITLEAKS_BLIND_CANARY"] = "1"
    # When export is attempted.
    result = _run(export_sandbox)
    # Then the unproven scan blocks the release instead of being trusted.
    assert result.returncode != 0
    assert not export_sandbox.target.exists()
    assert not _remote_has_ref(export_sandbox.public_remote, "refs/heads/main")
    assert not (export_sandbox.source / _CANARY_DIR).exists()


def test_canary_probes_reach_both_scan_roots_and_never_outlive_the_export(
    export_sandbox: ExportSandbox,
) -> None:
    # Given a successful export.
    result = _run(export_sandbox)
    assert result.returncode == 0, result.stderr

    # Then both scan roots were probed before their real scan ...
    scans = export_sandbox.gitleaks_log.read_text(encoding="utf-8").splitlines()
    assert sum(1 for line in scans if "--report-path" in line) == 2

    # ... and no canary survives in the private source or the published tree.
    assert not (export_sandbox.source / _CANARY_DIR).exists()
    assert not (export_sandbox.target / _CANARY_DIR).exists()
    tracked = _git(export_sandbox.target, "ls-tree", "-r", "--name-only", "HEAD")
    assert _CANARY_DIR not in tracked
