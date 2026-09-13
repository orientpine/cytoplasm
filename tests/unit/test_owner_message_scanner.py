"""Behavioral regressions for the static owner-message conformance scanner."""
from __future__ import annotations

import ast

import pytest

from tests.unit import test_owner_message_adoption_conformance as guard
from tests.unit.owner_message_conformance_ast import transport_calls


@pytest.mark.parametrize("body", [
    "def notify(discord): discord.send_owner_dm(raw)",
    "from automation.interop.origin_notice import deliver as send\ndef notify(): send(content=raw)",
    "from automation.owner_notice import notify_owner as send\ndef notify(): send(raw)",
    "from automation.owner_notice import notify_owner_dm as send\ndef notify(): send(raw)",
    "send = notify_owner\ndef notify(): send(raw)",
    "def notify():\n send = notify_owner\n send(raw)",
    "def notify(send=notify_owner): send(raw)",
    "def notify(*, send=notify_owner): send(raw)",
    "def notify(): return api('POST', f'/channels/{channel}/messages', payload)",
    "def notify(): return client.post('/channels/111/messages', json=payload)",
    "def notify(): return client.request(method='POST', url=f'/channels/{channel}/messages', json=payload)",
    "def notify(): return subprocess.run(['hermes', 'send', body])",
    "def notify():\n argv = ['hermes', 'send', body]\n subprocess.run(argv)",
    "def notify():\n argv = ['hermes']\n argv.append('send')\n subprocess.run(argv)",
    "from subprocess import run as execute\ndef notify(): execute(['hermes', 'send', body])",
    "import subprocess\ndef notify():\n prefix = ['hermes']\n argv = [*prefix, 'send', body]\n subprocess.run(argv)",
    "import subprocess as sp\ndef notify(): sp.run(['hermes', 'send', body])",
    "import subprocess\ndef notify(body):\n argv = ['hermes']\n argv = [*argv, 'send', body]\n subprocess.run(argv)",
    "import subprocess\ndef notify(body):\n if enabled:\n  argv = ['hermes', 'send', body]\n subprocess.run(argv)",
], ids=["injected-owner-dm", "deliver-import-alias", "owner-import-alias", "dm-import-alias", "module-rebinding",
        "local-rebinding", "positional-default", "keyword-default", "post-arbitrary-name",
        "post-method", "post-keywords", "hermes-literal", "hermes-argv", "hermes-append",
        "B1-imported-run", "B2-starred-argv", "A5-module-alias", "P12-reassignment", "A6-preceding-if"])
def test_raw_sender_is_rejected_when_hidden_by_alias_or_transport(
    body: str,
) -> None:
    # Given a new skill in isolation; the deployed graph is tested once separately.
    sources: dict[str, ast.Module] = {}
    sources["skills/example/scripts/probe.py"] = ast.parse(body)
    # When scanning the fixture through the real guard; the new sender must be named.
    with pytest.raises(AssertionError, match="skills/example/scripts/probe.py::notify"):
        guard.test_senders_use_envelopes_when_discovered(sources)


@pytest.mark.parametrize("body", [
    "import automation.interop.origin_notice as m\ndef notify(): m.deliver(message=envelope)",
    "from automation.interop.origin_notice import deliver as send\ndef notify(): send(message=envelope)",
    "send = notify_owner\ndef notify(): send(message=envelope)",
    "def notify(*, send=notify_owner): send(message=envelope)",
])
def test_adopted_alias_is_stale_when_still_exempt(
    body: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an adopted alias still registered as raw.
    key = "skills/example/scripts/probe.py::notify"
    sources = {key.split("::")[0]: ast.parse(body)}
    monkeypatch.setitem(guard._RAW_CONTENT_EXEMPT, key, "이관 전 예외")
    # When inventoried; then adoption is retained and the exemption is stale.
    sites, _, _ = guard._inventory(sources)
    assert sites.get(key) == [True]
    with pytest.raises(AssertionError, match=key):
        guard.test_raw_content_exemptions_are_not_stale(sources)


@pytest.mark.parametrize("body", [
    "deliver(content=raw)",
    "notify_owner(raw)",
    "notify_owner_dm(content=raw)",
    "deliver(content=raw, message=envelope)\n deliver(content='other')",
    "notify_owner(raw, message=envelope)\n notify_owner(other)",
    "notify_owner_dm(content=raw, message=envelope)\n notify_owner_dm(content=other)",
    "deliver(content=raw, message=envelope)\n notify_owner(raw)",
    "notify_owner(raw, message=envelope)\n notify_owner_dm(raw)",
    "deliver(message=envelope)\n deliver()",
    "notify_owner(message=envelope)\n notify_owner()",
    "deliver(raw, message=envelope)\n deliver(raw)",
], ids=["lone-deliver", "lone-owner", "lone-dm", "different-deliver-body",
        "different-owner-body", "different-dm-body", "different-deliver-facade",
        "different-owner-facade", "missing-deliver-body", "missing-owner-body",
        "positional-deliver-body"])
def test_fallback_is_rejected_when_pair_is_absent(body: str) -> None:
    # Given an unpaired content-only call in a synthetic sender.
    sources = {"probe.py": ast.parse("def notify():\n " + body)}
    # When the real inventory classifies the sender.
    sites, _, _ = guard._inventory(sources)
    # Then the scope still requires a raw-content exemption.
    assert not all(sites["probe.py::notify"])


@pytest.mark.parametrize("body", [
    "deliver(content=raw, message=envelope)",
    "notify_owner(raw, message=envelope)",
    "notify_owner_dm(content=raw, message=envelope)",
    "deliver(content=raw, message=envelope)\n deliver(content=other, message=another)",
])
def test_notice_is_adopted_when_every_call_has_message(body: str) -> None:
    # Given envelope calls, including multiple different bodies.
    sources = {"probe.py": ast.parse("def notify():\n " + body)}
    # When inventoried.
    sites, _, _ = guard._inventory(sources)
    # Then explicit adoption remains independent of body pairing.
    assert all(sites["probe.py::notify"])


@pytest.mark.parametrize("body", [
    "def notify():\n deliver(content=raw, message=envelope)\n def inner(): deliver(content=raw)",
    "def notify():\n deliver(content=raw)\n def inner(): deliver(content=raw, message=envelope)",
    "def notify(): deliver(content=raw)\ndef sibling(): deliver(content=raw, message=envelope)",
])
def test_fallback_is_rejected_when_envelope_belongs_to_another_scope(body: str) -> None:
    # Given identical bodies separated by innermost lexical scope.
    sources = {"probe.py": ast.parse(body)}
    # When inventoried.
    sites, _, _ = guard._inventory(sources)
    # Then exactly one synthetic scope is adopted, never both.
    assert sorted(all(calls) for key, calls in sites.items() if key.startswith("probe.py::")) == [False, True]


@pytest.mark.parametrize("body", [
    "def notify():\n if supported:\n  deliver(content=raw, message=envelope)\n else:\n  deliver(content=raw)",
    "def notify():\n if supported:\n  notify_owner(raw, message=envelope)\n else:\n  notify_owner(raw)",
    "def notify():\n notify_owner_dm(content=raw, message=envelope)\n notify_owner_dm(raw)",
    "def notify():\n notify_owner(raw, message=envelope)\n notify_owner(content=raw)",
    "from automation.interop.origin_notice import deliver as send\ndef notify():\n send(content=raw, message=envelope)\n deliver(content=raw)",
    "from automation.owner_notice import notify_owner as send\ndef notify():\n send(raw, message=envelope)\n notify_owner(raw)",
    "def outer(send=notify_owner):\n def notify():\n  send(content=raw)\n  send(raw, message=envelope)",
    "def notify():\n deliver(content=raw)\n deliver(content=other, message=one)\n deliver(content=raw, message=two)",
    "def notify():\n deliver(content=build(raw), message=envelope)\n deliver(content=build( raw ))",
], ids=["deliver-idiom", "owner-idiom", "dm-keyword", "owner-keyword", "deliver-alias",
        "owner-alias", "nested-default-alias", "any-envelope-reversed", "structural-body"])
def test_fallback_is_adopted_when_facade_and_body_match(body: str) -> None:
    # Given a capability fallback paired with the same resolved facade and body.
    sources = {"probe.py": ast.parse(body)}
    # When the real inventory classifies every synthetic scope.
    sites, _, _ = guard._inventory(sources)
    adopted = [calls for key, calls in sites.items() if key.startswith("probe.py::")]
    # Then every call is adopted, so the migrated sender needs no raw exemption.
    assert adopted and all(all(calls) for calls in adopted)


@pytest.mark.parametrize("scope", ["missing", "notify"])
def test_transport_exemption_is_stale_when_shape_disappears(
    scope: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a vanished scope or one that no longer sends channel messages.
    key = f"skills/example/scripts/probe.py::{scope}"
    sources = {key.split("::")[0]: ast.parse("def notify(): return api('GET', '/channels/111/messages')")}
    monkeypatch.setitem(guard._NOT_OWNER_FACING, key, "전송 내부 경로")
    # When checking non-owner exemptions; then stale entries are named.
    with pytest.raises(AssertionError, match=key):
        guard.test_transport_exemptions_are_not_stale(sources)


def test_transport_classification_is_exclusive_when_double_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a review sender wrongly classified in both audience registries.
    key = "skills/procurement/scripts/procure_review.py::send_review"
    monkeypatch.setitem(guard._RAW_CONTENT_EXEMPT, key, "검사 전용 원문 발신 분류")
    monkeypatch.setattr(guard, "_NOT_OWNER_FACING", {key: "잘못 중복 분류한 전송"})
    sources = {key.split("::")[0]: ast.parse("def send_review(client): client.send_owner_dm('raw')")}
    assert key in guard._scan(sources).transports
    # When classifying transports; then the contradictory row is named, not stale.
    with pytest.raises(AssertionError, match=f"conflicting transport audience:.*{key}"):
        guard.test_transport_exemptions_are_not_stale(sources)


@pytest.mark.parametrize("method", ["run", "call", "check_call", "check_output", "Popen"])
@pytest.mark.parametrize("binding", ["module", "module-alias", "name-alias"])
@pytest.mark.parametrize("local", [False, True], ids=["global-import", "local-import"])
def test_transport_is_discovered_when_subprocess_import_is_aliased(
    method: str, binding: str, local: bool,
) -> None:
    # Given each supported subprocess API and import placement.
    imports = {"module": "import subprocess", "module-alias": "import subprocess as sp",
               "name-alias": f"from subprocess import {method} as execute"}
    callees = {"module": f"subprocess.{method}", "module-alias": f"sp.{method}", "name-alias": "execute"}
    prefix = f"def notify():\n {imports[binding]}\n" if local else f"{imports[binding]}\ndef notify():\n"
    tree = ast.parse(prefix + f" {callees[binding]}(args=('hermes', 'send', body))")
    # When resolving transport shape; then its containing scope is retained.
    assert [scope for scope, _ in transport_calls(tree)] == ["notify"]


@pytest.mark.parametrize("body", [
    "prefix = ['hermes']\n argv = [*prefix, 'send', body]\n subprocess.run(argv)",
    "prefix = ('/bin/hermes',)\n argv = (*prefix, '--quiet', 'send', body)\n subprocess.run(args=argv)",
    "a = ['hermes']\n b = [*a]\n c = (*b,)\n argv = [*c, 'send']\n subprocess.run(argv)",
    "path = '/channels/111/messages'\n client.post(path, json=payload)",
    "path = f'/channels/{channel}/messages'\n endpoint = path\n client.request(method='POST', url=endpoint)",
], ids=["starred-list", "starred-tuple-ordered", "starred-depth-three", "post-name", "post-fstring-name"])
def test_transport_is_discovered_when_arguments_are_locally_resolvable(body: str) -> None:
    # Given literal/local argv or a local HTTP path, without executing it.
    tree = ast.parse("import subprocess\ndef notify():\n " + body.replace("\n ", "\n").replace("\n", "\n "))
    # When resolving the arguments; then the raw transport is inventoried.
    assert [scope for scope, _ in transport_calls(tree)] == ["notify"]


@pytest.mark.parametrize("body", [
    "client.run(['hermes', 'send'])",
    "subprocess.run(['send', 'hermes'])",
    "subprocess.run(['not-hermes', 'send'])",
    "subprocess.run(argv)",
    "subprocess.run(build_argv())",
    "getattr(subprocess, 'run')(['hermes', 'send'])",
    "prefix = prefix\n subprocess.run([*prefix, 'send'])",
    "a = ['hermes']\n b = [*a]\n c = [*b]\n d = [*c]\n subprocess.run([*d, 'send'])",
    "path = '/channels/111/messages'\n client.get(path)",
], ids=["unrelated-run", "reversed", "wrong-binary", "parameter", "returned-argv",
        "dynamic-attribute", "cyclic-name", "beyond-depth-three", "get-path"])
def test_transport_is_absent_when_outside_the_finite_shape(body: str) -> None:
    # Given unsupported syntax or a different transport, never execute the source.
    tree = ast.parse("import subprocess\ndef notify(argv):\n " + body.replace("\n ", "\n").replace("\n", "\n "))
    # When scanning the bounded closure; then no owner-send transport is inferred.
    assert list(transport_calls(tree)) == []


@pytest.mark.parametrize(("body", "expected"), [
    ("subprocess.run(argv)\n argv = ['hermes', 'send']", []),
    ("argv = subprocess.run([*argv, 'send'])\n argv = ['hermes']", []),
    ("argv = ['hermes']\n argv = subprocess.run([*argv, 'send'])", ["notify"]),
    ("argv = ['hermes']; argv = [*argv, 'send']; subprocess.run(argv)", ["notify"]),
    ("argv = ['hermes', 'send']\n argv = ['hermes', 'status']\n subprocess.run(argv)", []),
    ("prefix = ['hermes']\n argv = [*prefix, 'send']\n prefix = ['other']\n subprocess.run(argv)", ["notify"]),
    ("argv = [*prefix, 'send']\n prefix = ['hermes']\n subprocess.run(argv)", []),
], ids=["A6-use-before-assignment", "A6-unbound-rhs", "A6-preceding-rhs", "A6-same-line",
        "A6-nearest-binding", "A6-prefix-at-use", "A6-future-prefix"])
def test_transport_resolution_when_bindings_change_in_source_order(body: str, expected: list[str]) -> None:
    # Given source-ordered assignments; an RHS use cannot see its own or a future binding.
    tree = ast.parse("import subprocess\ndef notify():\n " + body.replace("\n ", "\n").replace("\n", "\n "))
    # When resolving each use; then only the nearest preceding binding contributes.
    assert [scope for scope, _ in transport_calls(tree)] == expected
