"""Typing decorators shared with Python 3.11 no-agent runtimes."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from typing import override
else:
    try:
        from typing import override
    except ImportError:
        _Method = TypeVar("_Method")

        def override(method: _Method, /) -> _Method:
            return method

__all__ = ["override"]
