"""De-identify immutable vendored files in a materialized public snapshot."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Final


class PublicExportRedactionError(RuntimeError):
    """A vendored public-export copy could not be safely de-identified."""


_INSTITUTIONAL_EMAIL_DOMAIN: Final = re.compile(
    r"@(?:[A-Za-z0-9-]+\.)+(?:ac|re|go)\.kr\b"
)
_KOREAN_ORGANIZATION: Final = re.compile(
    r"[A-Za-z0-9가-힣]+(?:연구원|연구소)(?:\s*-\s*[A-Za-z0-9가-힣]+)?"
)
_ACCOUNT_NAME: Final = re.compile(
    r'(?m)^(?P<prefix>\s*me\s*=\s*f.*?)[가-힣]{2,4}(?P<suffix>.*<\{ACCOUNT\}>.*)$'
)
_PRODUCTION_HOST: Final = re.compile(r"\bori[0-9a-z]+\b")
_VALIDATED_HOST: Final = re.compile(
    r"(?<=validated on prod \()[^,\n]+(?=,\s*CPython)"
)
_TARGET_RULES: Final = {
    "skills/mail/vendor/mailon/resolve.py": (
        (_KOREAN_ORGANIZATION, "<example-organization>"),
    ),
    "skills/mail/vendor/requirements.txt": (
        (_VALIDATED_HOST, "<primary-node>"),
    ),
    "skills/mail/vendor/tests/test_offline.py": (
        (_INSTITUTIONAL_EMAIL_DOMAIN, "@example.invalid"),
        (_ACCOUNT_NAME, r"\g<prefix><owner-name>\g<suffix>"),
        (_KOREAN_ORGANIZATION, "<example-organization>"),
    ),
}
_POSTCONDITIONS: Final = {
    "skills/mail/vendor/mailon/resolve.py": (_KOREAN_ORGANIZATION,),
    "skills/mail/vendor/requirements.txt": (_PRODUCTION_HOST,),
    "skills/mail/vendor/tests/test_offline.py": (
        _INSTITUTIONAL_EMAIL_DOMAIN,
        _ACCOUNT_NAME,
        _KOREAN_ORGANIZATION,
    ),
}


def redact_vendor_tree(snapshot_root: Path) -> None:
    """Rewrite only exported copies of byte-preserved vendor files."""
    for relative, rules in _TARGET_RULES.items():
        path = snapshot_root / relative
        if not path.is_file():
            continue
        try:
            redacted = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise PublicExportRedactionError(f"cannot read vendored export copy: {relative}") from error
        for pattern, replacement in rules:
            redacted = pattern.sub(replacement, redacted)
        for forbidden in _POSTCONDITIONS[relative]:
            if forbidden.search(redacted) is not None:
                raise PublicExportRedactionError(
                    f"vendored export copy still matches private-data rule: {relative}"
                )
        try:
            path.write_text(redacted, encoding="utf-8")
        except OSError as error:
            raise PublicExportRedactionError(f"cannot write vendored export copy: {relative}") from error


# The manifest decides WHICH files are published; until 2026-09-07 nothing decided what
# VALUES they carried. An installation's tailnet address and production hostname reached
# the public repository and were found by a third-party installer reading the code, not
# by a check. Deleting those values closes one instance; this closes the class.
#
# Scope is measured, not assumed. Over the exported set these two patterns match six
# times with no false positive, while RFC1918 matches a dependency lockfile and a
# synthetic corpus, and a loose ``ori[0-9a-z]+`` matches 1484 times (``origin``,
# ``orientpine``, ``original``). A guard that cries wolf earns an exception list, and an
# exception list is how this class comes back.
_TAILNET_ADDRESS: Final = re.compile(
    r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"
)
_PRODUCTION_NODE_HOST: Final = re.compile(r"\bori[0-9a-f]{4,}\b")
_TOPOLOGY_RULES: Final = (
    ("tailnet address", _TAILNET_ADDRESS),
    ("production node hostname", _PRODUCTION_NODE_HOST),
)


def assert_no_private_topology(snapshot_root: Path) -> None:
    """Refuse a snapshot that still names one installation's addresses or hosts.

    Runs AFTER :func:`redact_vendor_tree`, so the byte-preserved vendor copies it already
    de-identifies are judged in their published form rather than their tracked one.

    Undecodable files are skipped: this reads published source, and the directories that
    hold binary evidence are excluded from the export outright. A readable neighbour is
    still judged, so one archive cannot silence the check.
    """
    offences: list[str] = []
    for path in sorted(snapshot_root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        relative = path.relative_to(snapshot_root).as_posix()
        for label, pattern in _TOPOLOGY_RULES:
            found = sorted({match.group(0) for match in pattern.finditer(text)})
            if found:
                offences.append(f"{relative}: {label} {', '.join(found)}")
    if offences:
        raise PublicExportRedactionError(
            "public snapshot still carries installation topology:\n  "
            + "\n  ".join(offences)
        )


def main() -> int:
    if len(sys.argv) != 2:
        raise PublicExportRedactionError("usage: public_export_redaction.py SNAPSHOT_ROOT")
    snapshot_root = Path(sys.argv[1])
    redact_vendor_tree(snapshot_root)
    assert_no_private_topology(snapshot_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
