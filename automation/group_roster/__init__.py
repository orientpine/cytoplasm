"""Research-group roster model, validation, and signed distribution primitives."""

from .fetch import (
    ROSTER_REF,
    ROSTER_SIGNATURE_NAMESPACE,
    RosterFetchConfig,
    RosterFetchError,
    RosterFetchResult,
    refresh_roster,
)
from .parser import (
    DEFAULT_ROSTER_PATH,
    ROSTER_ENV,
    load_roster,
    parse_roster,
    roster_path,
)
from .schema import SCHEMA_VERSION, MemberStatus, Roster, RosterAdmin, RosterMember
from .validator import RosterError, YamlKey, YamlMapping, YamlValue, validate_roster

__all__ = [
    "DEFAULT_ROSTER_PATH",
    "MemberStatus",
    "ROSTER_ENV",
    "ROSTER_REF",
    "ROSTER_SIGNATURE_NAMESPACE",
    "Roster",
    "RosterAdmin",
    "RosterError",
    "RosterFetchConfig",
    "RosterFetchError",
    "RosterFetchResult",
    "RosterMember",
    "SCHEMA_VERSION",
    "YamlKey",
    "YamlMapping",
    "YamlValue",
    "load_roster",
    "parse_roster",
    "refresh_roster",
    "roster_path",
    "validate_roster",
]
