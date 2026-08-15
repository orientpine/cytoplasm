"""Shared publisher identity formats for the managed-skill channel.

One group has exactly one publisher, named twice in the wire protocol:

- ``publisher`` — the manifest field and the privileged installer's release
  directory component (``managed-releases/<publisher>/<skill>``).
- ``publisher_principal`` — the e-mail principal of the SSH-signed release tag,
  the value ``git verify-tag`` echoes and the subscriber pins.

Both formats are already fixed elsewhere (roster schema v1 validates the
principal; the privileged skill store validates the name). This module is the
single place the managed-skill channel reads them from, so the publisher side
can reject a bad identity at publish time instead of at a subscriber's install.
``tests/unit/test_managed_principal.py`` pins these patterns to the two
authorities mechanically — the formats cannot drift apart silently.
"""

from __future__ import annotations

import re
from typing import Final

PUBLISHER_PRINCIPAL: Final = re.compile(
    r"\Apublisher-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?@autophagy\Z"
)
PUBLISHER_NAME: Final = re.compile(r"\A[a-z0-9][a-z0-9-]{0,31}\Z")


def is_publisher_principal(value: object) -> bool:
    """Report whether one value is a well-formed group publisher principal."""
    return isinstance(value, str) and PUBLISHER_PRINCIPAL.fullmatch(value) is not None


def is_publisher_name(value: object) -> bool:
    """Report whether one value is a well-formed manifest publisher name."""
    return isinstance(value, str) and PUBLISHER_NAME.fullmatch(value) is not None
