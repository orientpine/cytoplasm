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


def main() -> int:
    if len(sys.argv) != 2:
        raise PublicExportRedactionError("usage: public_export_redaction.py SNAPSHOT_ROOT")
    redact_vendor_tree(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
