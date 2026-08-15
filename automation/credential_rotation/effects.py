from __future__ import annotations

import os
import pwd
import secrets
import string
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .files import FileWriter, atomic_write
from .registry import HttpProbe, ProviderContext, RotationError, RotationTarget

PASSWORD_ALPHABET: Final = string.ascii_letters + string.digits
_PROVIDER_HASH_SCRIPT: Final = """import sys
from plugins.dashboard_auth.basic import _verify_password, hash_password
password = sys.stdin.read()
encoded = hash_password(password)
if not _verify_password(password, encoded):
    raise SystemExit(1)
sys.stdout.write(encoded)
"""


class ProbeSender(Protocol):
    def __call__(self, probe: HttpProbe) -> int:
        ...


class UnitRestarter(Protocol):
    def __call__(self, target: RotationTarget) -> None:
        ...


class SecureDeleter(Protocol):
    def __call__(self, path: Path) -> None:
        ...


@dataclass(frozen=True, slots=True)
class RotationSeams:
    """Effects supplied by production or replaced with offline test doubles."""

    current_user: Callable[[], str]
    generate_password: Callable[[], str]
    generate_session_secret: Callable[[], str]
    provider_hash: Callable[[ProviderContext, str], str]
    restart_unit: UnitRestarter
    send_probe: ProbeSender
    atomic_write: FileWriter
    secure_delete: SecureDeleter
    now: Callable[[], datetime]


def new_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(24))


def current_user() -> str:
    """Identify the effective account, rather than a sudo-inherited login environment."""
    return pwd.getpwuid(os.geteuid()).pw_name


def provider_hash(provider: ProviderContext, password: str) -> str:
    """Run the installed provider's hash-plus-verify code with stdin-only secrets."""
    completed = subprocess.run(
        (str(provider.interpreter), "-c", _PROVIDER_HASH_SCRIPT),
        check=False,
        cwd=provider.working_directory,
        input=password,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    encoded = completed.stdout.strip()
    if completed.returncode != 0 or not encoded:
        raise RotationError("dashboard provider rejected the generated password")
    return encoded


def restart_unit(target: RotationTarget) -> None:
    environment = dict(os.environ)
    environment["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
    subprocess.run(
        ("systemctl", "--user", "restart", target.unit_name),
        check=True,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


_PROBE_TIMEOUT_SECONDS: Final = 10.0
_READY_DEADLINE_SECONDS: Final = 30.0
_READY_POLL_SECONDS: Final = 0.25


def send_probe(probe: HttpProbe) -> int:
    """Return the HTTP status, waiting out the unit's restart before giving up.

    A just-restarted unit needs a moment to bind (measured ~650 ms for the Kanban
    dashboard), and urlopen raises OSError until it does. Reporting that as an
    answer reads "service is not up yet" as "wrong password" and triggers a
    pointless rollback, which is exactly how the first live run failed.

    Retrying cannot trip the dashboard's login rate limiter: a transport failure
    means the request never reached the application, so no login attempt was
    recorded. Any real HTTP status - 401 included - is an answer and returns
    immediately.
    """
    deadline = time.monotonic() + _READY_DEADLINE_SECONDS
    while True:
        request = Request(
            probe.url, data=probe.body, headers=dict(probe.headers), method=probe.method
        )
        try:
            with urlopen(request, timeout=_PROBE_TIMEOUT_SECONDS) as response:  # noqa: S310 (registry-only tailnet URLs)
                return response.getcode() or 0
        except HTTPError as error:
            return error.code
        except OSError:
            if time.monotonic() >= deadline:
                return 0
            time.sleep(_READY_POLL_SECONDS)


def secure_delete(path: Path) -> None:
    """Prefer shredding credential backups, with unlink only as the documented fallback."""
    try:
        subprocess.run(
            ("shred", "-u", str(path)),
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        path.unlink(missing_ok=True)


DEFAULT_SEAMS: Final = RotationSeams(
    current_user=current_user,
    generate_password=new_password,
    generate_session_secret=lambda: secrets.token_hex(32),
    provider_hash=provider_hash,
    restart_unit=restart_unit,
    send_probe=send_probe,
    atomic_write=atomic_write,
    secure_delete=secure_delete,
    now=lambda: datetime.now(UTC),
)
