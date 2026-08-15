from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "automation" / "deploy-skill.sh"


def test_deploy_peer_attest_fn_when_invoked_then_threads_channel_id_to_attestor() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When / Then: the peer_attest shell fn accepts a 5th positional and forwards it.
    def_idx = script.index("peer_attest() {")
    body = script[def_idx : script.index("\n}", def_idx)]
    assert 'channel="$5"' in body
    assert '--channel-id \\"$channel\\"' in body


def test_deploy_stage3_when_synced_then_resolves_deploy_approvals_channel_via_agent() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When / Then: the channel id is read once from the agent's interop config, fail-open to "".
    sync_call_idx = script.index("\nsync_ops_checkout_for_peer_attest\n")
    resolve_idx = script.index('DEPLOY_APPROVALS_CHANNEL_ID="$(run_as "$NODE_AGENT_ACCOUNT"')
    assert sync_call_idx < resolve_idx
    resolve_line = script[resolve_idx : script.index("\n", resolve_idx)]
    assert "deploy_approvals_channel_id" in resolve_line
    assert "|| true" in resolve_line


def test_deploy_stage3_call_site_when_attesting_then_passes_resolved_channel_as_fifth_arg() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When / Then: the stage-3 call site threads the resolved id as the 5th argument.
    call = 'peer_attest "$SKILL" "$DIGEST" "$MESSAGE_ID" "$DEPLOY_NONCE" "$DEPLOY_APPROVALS_CHANNEL_ID"'
    resolve_idx = script.index('DEPLOY_APPROVALS_CHANNEL_ID="$(run_as "$NODE_AGENT_ACCOUNT"')
    call_idx = script.index(call, resolve_idx)
    assert resolve_idx < call_idx
