"""The optional renderer cannot break delivery or take ownership of fallback errors."""
from __future__ import annotations

import builtins
from dataclasses import replace

import pytest

from automation.interop import origin_notice, owner_message
from test_origin_notice_compatibility import DeliverySurface
from test_origin_notice_owner_message import MESSAGE, RECORD, envelope_options


@pytest.mark.parametrize("route", ["thread", "failure", "absent"])
@pytest.mark.parametrize("failure", ["version", "renderer", "import"])
def test_plain_delivery_when_optional_rendering_fails(monkeypatch, capsys, route, failure):
    # Given: the optional contract or runtime can fail independently on either surface.
    surface = DeliverySurface(monkeypatch)
    surface.fail_send = route == "failure"
    message = MESSAGE
    error_name = "OwnerMessageError"
    if failure == "version":
        message = replace(message, render_version="future-version")
    if failure == "renderer":
        def render(message, *, destination):
            raise RuntimeError("renderer unavailable")
        monkeypatch.setattr(owner_message, "render", render)
        error_name = "RuntimeError"
    if failure == "import":
        original_import = builtins.__import__

        # Five parameters mirror the Python import hook, not a new domain API.
        def import_module(name, globals=None, locals=None, fromlist=(), level=0):
            if name.endswith("owner_message"):
                raise ImportError("optional runtime absent")
            return original_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", import_module)
        error_name = "ImportError"
    # When: a well-defined plain body remains available as compatibility fallback.
    result = origin_notice.deliver(
        api=surface.api, transport_factory=surface.transport,
        record={} if route == "absent" else RECORD,
        thread_name="request", content=" plain\r\n✅ ", fallback=surface.fallback,
        **envelope_options(message),
    )
    # Then: every attempted path delivers the exact plain bytes and marks render failure.
    assert (surface.posts if route == "thread" else surface.fallbacks) == [" plain\r\n✅ "]
    assert result == ("501" if route == "thread" else surface.receipt)
    lines = capsys.readouterr().err.splitlines()
    record_id = "" if route == "absent" else "request"
    marker = f"NOTIFY-RENDER-FAIL id={record_id} err={error_name}"
    assert lines.count(marker) == (2 if route == "failure" else 1)
    assert lines.count("NOTIFY-THREAD-FAIL id=request err=HTTPError") == (route == "failure")


def test_no_optional_import_when_only_content_is_given(monkeypatch):
    # Given: an older runtime with no optional envelope module at all.
    surface = DeliverySurface(monkeypatch)
    original_import = builtins.__import__
    imports: list[str] = []

    # Python's import-hook signature is fixed by the interpreter.
    def import_module(name, globals=None, locals=None, fromlist=(), level=0):
        if name.endswith("owner_message"):
            imports.append(name)
            raise ImportError("optional runtime absent")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_module)
    # When: the legacy content-only route is used.
    result = origin_notice.deliver(
        api=surface.api, transport_factory=surface.transport, record=RECORD,
        thread_name="request", content="plain", fallback=surface.fallback,
    )
    # Then: content delivery never even attempts the optional import.
    assert result == "501" and surface.posts == ["plain"]
    assert imports == []


@pytest.mark.parametrize("origin", [False, True])
def test_caller_owns_fallback_failure_when_envelope_is_given(monkeypatch, origin):
    # Given: a working renderer and a failing caller-supplied fallback.
    surface = DeliverySurface(monkeypatch)
    surface.fail_send = True
    error = OSError("fallback unavailable")

    def fallback(body: str) -> str:
        raise error

    # When: delivery reaches either fallback route.
    with pytest.raises(OSError) as raised:
        origin_notice.deliver(
            api=surface.api, transport_factory=surface.transport, record=RECORD if origin else {},
            thread_name="request", content="plain", fallback=fallback,
            **envelope_options(MESSAGE),
        )
    # Then: the original fallback exception is not swallowed by renderer protection.
    assert raised.value is error
