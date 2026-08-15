from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "automation" / "deploy-skill.sh"


def test_deploy_pins_repo_root_to_one_physical_path_at_entry() -> None:
    # Given: a deploy may start through the mutable release-current symlink.
    script = DEPLOY.read_text(encoding="utf-8")

    # When: the repository root is established at pipeline entry.
    assignment = next(line for line in script.splitlines() if line.startswith("REPO_ROOT="))

    # Then: it resolves symlinks immediately and every later consumer reuses REPO_ROOT.
    assert "pwd -P" in assignment
    assert script.count("REPO_ROOT=") == 1


def test_approve_only_reports_that_it_does_not_mount() -> None:
    # Given: approve-only exits successfully immediately before stage 4.
    script = DEPLOY.read_text(encoding="utf-8")
    start = script.index('if [[ "$APPROVE_ONLY" == 1 ]]')
    body = script[start : script.index("\nfi", start)]

    # When / Then: both operator-facing usage and the success path name the no-mount contract.
    assert "verify approval only; do not mount" in script
    assert "approval verified; NOT mounting" in body


def _lists_skill(table: str, name: str) -> bool:
    """Run the deploy script's OWN grep pattern against real `hermes skills list` output."""
    return (
        subprocess.run(
            ["grep", "-Fq", f"\u2502 {name} "],
            input=table,
            text=True,
            check=False,
        ).returncode
        == 0
    )


def test_hermes_skill_lookup_matches_the_name_cell_of_the_rendered_table() -> None:
    # Given: `hermes skills list` renders a bordered table, and managed-X and X may
    # both be valid, distinct skill names. a824c4f asked grep for a whole-LINE match,
    # which no table row can ever satisfy — every deploy died at SANDBOX until
    # 2026-08-04. Assert against the REAL output shape, not just the source string.
    script = DEPLOY.read_text(encoding="utf-8")
    start = script.index("hermes_lists_skill() {")
    body = script[start : script.index("\n}", start)]
    assert "grep -Fxq" not in body, "a whole-line match cannot match a table row"

    table = (
        "\u250f\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2513\n"
        "\u2503 Name                    \u2503\n"
        "\u2502 mail                    \u2502\n"
        "\u2502 managed-hello-autophagy \u2502\n"
    )

    # When / Then: a listed skill is found, and `X` never matches `managed-X`.
    assert _lists_skill(table, "mail")
    assert _lists_skill(table, "managed-hello-autophagy")
    assert not _lists_skill(table, "hello-autophagy")
    assert not _lists_skill(table, "wiki")


def test_deploy_when_stage3_starts_then_syncs_ops_checkout_before_request_and_attest() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When / Then
    def_idx = script.index("sync_ops_checkout_for_peer_attest() {")
    call_idx = script.index("\nsync_ops_checkout_for_peer_attest\n")
    assert def_idx < call_idx
    assert call_idx < script.index('if [[ -n "${APPROVAL_MESSAGE_ID:-}" ]]')
    assert call_idx < script.index('request --skill "$SKILL"')
    assert call_idx < script.index('peer_attest "$SKILL" "$DIGEST"', call_idx)


def test_deploy_sync_when_pulled_then_verifies_verifier_file_hashes_via_ops_account() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When / Then
    def_idx = script.index("sync_ops_checkout_for_peer_attest() {")
    body = script[def_idx : script.index("\n}", def_idx)]
    assert 'run_as "$NODE_OPS_ACCOUNT"' in body
    assert "git -C $NODE_DEPLOY_CHECKOUT pull --ff-only" in body
    assert "rev-parse --is-inside-work-tree" in body
    assert "status --porcelain" in body
    assert "sha256sum" in body
    for fname in ("peer_attest.py", "peer_attestation.py", "skill_review.py"):
        assert fname in body


def test_deploy_sync_when_ops_checkout_unhealthy_then_fails_closed_with_clear_messages() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When / Then
    def_idx = script.index("sync_ops_checkout_for_peer_attest() {")
    body = script[def_idx : script.index("\n}", def_idx)]
    assert "SYNC-BLOCK: $NODE_DEPLOY_CHECKOUT is not a git checkout" in body
    assert "SYNC-BLOCK: ops checkout is dirty (local modifications present)" in body
    assert "SYNC-BLOCK: ff-only pull failed on ops checkout" in body
    assert "differs between local repo and ops checkout" in body


# --- DG-4 W3.D: deploy-skill.sh becomes runtime-root aware, fallback-safe ---

def test_deploy_sources_the_runtime_root_resolver() -> None:
    # The shell runtime-root resolver is available so paths are not bare literals.
    script = DEPLOY.read_text(encoding="utf-8")
    assert "runtime_root.sh" in script
    assert "autophagy_runtime_root" in script


def test_deploy_prefers_release_current_when_present() -> None:
    # When the release runtime exists, the sync path converges a pinned snapshot
    # release and flips current BEFORE peer attestation, instead of ff-pulling the
    # mutable mirror. A parallel session's dirty mirror can no longer block deploy.
    script = DEPLOY.read_text(encoding="utf-8")
    def_idx = script.index("sync_ops_checkout_for_peer_attest() {")
    body = script[def_idx : script.index("\n}", def_idx)]
    assert 'RELEASE_CURRENT="$NODE_RELEASE_CURRENT"' in script
    assert "$RELEASE_CURRENT" in body
    assert "converge-release-runtime.sh" in body
    # the converge helper is where the snapshot primitive is invoked
    converge = (ROOT / "automation" / "converge-release-runtime.sh").read_text(encoding="utf-8")
    assert "origin_snapshot" in converge
    assert "autophagy-install-release" in converge


def test_deploy_release_probe_failure_reports_ops_access_error() -> None:
    # Given: run_as itself can fail before the node can report presence or absence.
    script = DEPLOY.read_text(encoding="utf-8")
    start = script.index("sync_ops_checkout_for_peer_attest() {")
    body = script[start : script.index("\n}", start)]

    # When / Then: the probe failure is not allowed to fall through as release absence.
    assert 'release_state="$(run_as "$NODE_OPS_ACCOUNT"' in body
    assert "release runtime probe failed (ops sudo/permission denied)" in body


def test_deploy_release_absence_explicitly_selects_mirror_fallback() -> None:
    # Given: a successful ops probe can positively report that current is absent.
    script = DEPLOY.read_text(encoding="utf-8")
    start = script.index("sync_ops_checkout_for_peer_attest() {")
    body = script[start : script.index("\n}", start)]

    # When / Then: only that explicit state reaches the historical mirror path.
    assert '"absent")' in body
    assert "release runtime absent; using ops checkout fallback" in body


def test_deploy_falls_back_to_ff_pull_when_current_absent() -> None:
    # Backwards-compatible: with no current symlink, the existing ff-pull path is
    # preserved verbatim, so merging PR-A is a live no-op until the DG-5 flip.
    script = DEPLOY.read_text(encoding="utf-8")
    def_idx = script.index("sync_ops_checkout_for_peer_attest() {")
    body = script[def_idx : script.index("\n}", def_idx)]
    assert "git -C $NODE_DEPLOY_CHECKOUT pull --ff-only" in body
    assert "SYNC-BLOCK: ff-only pull failed on ops checkout" in body


# --- DG-5: trust-critical exec paths resolve the runtime root NODE-SIDE ---

def test_peer_attest_and_lock_run_from_a_node_resolved_runtime_root() -> None:
    # peer_attest / execution-lock / chmod run via `run_as <acct>` ON THE NODE, so
    # the runtime root must be resolved inside that node-side shell, not interpolated
    # from the workstation-resolved $RUNTIME_ROOT (which stats a node path on the
    # wrong host). A node-side helper resolves current-else-mirror in the run_as body.
    script = DEPLOY.read_text(encoding="utf-8")
    # a node-side resolver snippet exists and is used by the trust-critical run_as calls
    assert "node_runtime_root" in script
    # the peer_attest invocation no longer hardcodes the mirror python path
    peer_line = next(line for line in script.splitlines() if "peer_attest.py" in line and "--skill" in line)
    assert "/srv/autophagy-agents/automation/peer_attest.py" not in peer_line
    assert "node_runtime_root" in peer_line
    # the execution lock no longer hardcodes the mirror
    lock_line = next(line for line in script.splitlines() if "deploy_execution_lock.py" in line and "--skill" in line)
    assert "PYTHONPATH=/srv/autophagy-agents " not in lock_line
    assert "/srv/autophagy-agents/automation/deploy_execution_lock.py" not in lock_line
    assert "node_runtime_root" in lock_line


def test_node_runtime_root_helper_resolves_current_else_mirror() -> None:
    # The node-side helper receives the already-resolved release and mirror paths.
    script = DEPLOY.read_text(encoding="utf-8")
    def_idx = script.index("node_runtime_root()")
    body = script[def_idx : script.index("\n}", def_idx)]
    assert "$NODE_RELEASE_CURRENT" in body
    assert "$NODE_DEPLOY_CHECKOUT" in body


def test_converge_helper_uses_canonical_paths_and_sudo_install() -> None:
    # DG-5 path-convention fix: converge installs to the canonical release layout
    # via `sudo -n` (ops running a root-owned helper does not gain root), and the
    # read-only `current --verify` runs without sudo.
    converge = (ROOT / "automation" / "converge-release-runtime.sh").read_text(encoding="utf-8")
    # the privileged install line (command, not a comment) carries sudo -n
    cmd_lines = [line for line in converge.splitlines() if not line.lstrip().startswith("#")]
    install_line = next(line for line in cmd_lines if "install --sha" in line)
    assert "sudo -n" in install_line
    # the read-only verify line does NOT need sudo
    verify_line = next(line for line in cmd_lines if "current --verify" in line)
    assert "sudo" not in verify_line
    assert "RELEASE_STORE_PARENT:-$NODE_SERVICE_ROOT" in converge
    # release_store.py owns the canonical basenames (not a bare literal here)
    rs = (ROOT / "automation" / "release_store.py").read_text(encoding="utf-8")
    assert '_RELEASES_BASENAME: Final = "autophagy-agent-releases"' in rs
    assert '_CURRENT_BASENAME: Final = "autophagy-agent-current"' in rs


def test_release_store_never_uses_the_generic_layout_names() -> None:
    # Lock the 2026-07-31 rollout bug shut: no bare store_root/"releases" or
    # store_root/"current" that would land at /srv/releases + /srv/current.
    rs = (ROOT / "automation" / "release_store.py").read_text(encoding="utf-8")
    assert 'store_root / "releases"' not in rs
    assert 'store_root / "current"' not in rs


# --- DG-6: the converger becomes safe to call from a landing ------------------


def test_converge_honours_an_explicitly_pinned_sha() -> None:
    # land.sh must converge the runtime to the sha IT pushed. Left to re-read
    # origin the converger would install whatever landed most recently instead,
    # so the caller's post-condition would be checking someone else's landing.
    converge = (ROOT / "automation" / "converge-release-runtime.sh").read_text(encoding="utf-8")
    assert "RELEASE_EXPECTED_SHA" in converge
    cmd_lines = [line for line in converge.splitlines() if not line.lstrip().startswith("#")]
    ls_remote = next(line for line in cmd_lines if "ls-remote" in line)
    # origin is only consulted when no sha was pinned
    assert "RELEASE_EXPECTED_SHA" in ls_remote or any(
        "RELEASE_EXPECTED_SHA" in line and "ls-remote" not in line for line in cmd_lines
    )


def test_converge_sources_the_snapshot_primitive_from_its_own_tree() -> None:
    # DG-6 downgrades a dirty mirror to a warning; that is only sound while we
    # stop EXECUTING the mirror's shell. Sourcing the snapshot primitive out of
    # $MIRROR would run a parallel session's uncommitted code as ops.
    converge = (ROOT / "automation" / "converge-release-runtime.sh").read_text(encoding="utf-8")
    source_line = next(
        line for line in converge.splitlines()
        if line.lstrip().startswith("source") and "origin_snapshot.sh" in line
    )
    assert "$MIRROR" not in source_line
    assert "BASH_SOURCE" in source_line


def test_converge_serializes_the_install_and_flip() -> None:
    # `current` is flipped by both land.sh and deploy-skill.sh. origin_snapshot's
    # own lock is released before the command runs, so without a shared lock a
    # slow older convergence can flip the runtime BACKWARDS over a newer one.
    converge = (ROOT / "automation" / "converge-release-runtime.sh").read_text(encoding="utf-8")
    assert "flock" in converge
    # ...on a path that does not move with the caller's environment: a lock two
    # callers resolve differently is not a lock.
    lock_line = next(line for line in converge.splitlines() if line.startswith("LOCK="))
    assert "TMPDIR" not in lock_line


def test_deploy_runs_the_converger_from_the_runtime_root_not_the_mirror() -> None:
    script = DEPLOY.read_text(encoding="utf-8")
    def_idx = script.index("sync_ops_checkout_for_peer_attest() {")
    body = script[def_idx : script.index("\n}", def_idx)]
    converge_line = next(line for line in body.splitlines() if "converge-release-runtime.sh" in line)
    assert "/srv/autophagy-agents/automation/converge-release-runtime.sh" not in converge_line
    assert "$RELEASE_CURRENT" in converge_line



def test_deploy_can_replace_a_read_only_tree_it_shipped_earlier(tmp_path: Path) -> None:
    # Given: E9 seals releases 0555/0444 and tar preserves those modes, so the
    # PREVIOUS deploy's copy lands unwritable. A directory without write permission
    # cannot have its entries removed even by their owner, so a plain `rm -rf` died
    # with "Permission denied" in both the peer sandbox and agent review staging
    # (measured 2026-08-04, blocking every redeploy).
    for function in ("push_skill() {", "stage_review_source() {"):
        script = DEPLOY.read_text(encoding="utf-8")
        start = script.index(function)
        body = script[start : script.index("\n}", start)]
        assert "chmod -R u+w" in body, f"{function} must unseal before removing"
        assert body.index("chmod -R u+w") < body.index("rm -rf"), "unseal must precede removal"

    # When / Then: the shipped shape is actually removable. Reproduce it for real.
    sealed = tmp_path / "skills" / "mail"
    (sealed / "vendor").mkdir(parents=True)
    (sealed / "vendor" / "scraper.py").write_text("x", encoding="utf-8")
    (sealed / "vendor" / "scraper.py").chmod(0o444)
    (sealed / "vendor").chmod(0o555)
    sealed.chmod(0o555)

    plain = subprocess.run(["rm", "-rf", str(sealed)], capture_output=True, check=False)
    assert sealed.exists(), "a sealed tree must NOT be removable without unsealing"
    assert plain.returncode != 0 or sealed.exists()

    _ = subprocess.run(["chmod", "-R", "u+w", str(sealed)], check=False)
    _ = subprocess.run(["rm", "-rf", str(sealed)], check=False)
    assert not sealed.exists(), "unsealing first must make the tree removable"
