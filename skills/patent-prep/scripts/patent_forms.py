"""Static local-only templates for a patent preparation workspace."""

from __future__ import annotations

from typing import Final


DISCLOSURE_FORM: Final = """# Invention Disclosure Form

## Problem and context

## Technical approach

## Distinguishing features

## Evidence and examples

## Inventors and contributors

## Public disclosure and timing

## Technology-transfer considerations
"""

PRIOR_ART_CHECKLIST: Final = """# Prior-Art Checklist

- [ ] Define the problem, proposed distinction, and search terms.
- [ ] Search patent databases and record comparable publications.
- [ ] Search academic and public technical literature.
- [ ] Compare each reference against distinguishing features.
- [ ] Identify public disclosures, owners, dates, and follow-up questions.
- [ ] Review the result with the responsible technology-transfer contact.
"""


def disclosure_form() -> str:
    """Return the local invention-disclosure form template."""
    return DISCLOSURE_FORM


def prior_art_checklist() -> str:
    """Return the local prior-art review checklist template."""
    return PRIOR_ART_CHECKLIST
