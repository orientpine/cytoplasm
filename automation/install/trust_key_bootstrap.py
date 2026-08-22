"""Update/group signing-key adapters over the shared allowed-signers boundary."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Final, Literal, TypeAlias

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(REPO_ROOT))

from automation.install.allowed_signers import (  # noqa: E402
    DEFAULT_UPDATE_TRUST_PRINCIPAL as DEFAULT_UPDATE_TRUST_PRINCIPAL,
    GIT_SIGNATURE_NAMESPACE as GIT_SIGNATURE_NAMESPACE,
    GROUP_SIGNATURE_NAMESPACES as GROUP_SIGNATURE_NAMESPACES,
    GROUP_SKILL_TRUST_TARGET,
    MANAGED_SKILLS_ALLOWED_SIGNERS_PATH as MANAGED_SKILLS_ALLOWED_SIGNERS_PATH,
    REQUIRED_MODE as REQUIRED_MODE,
    ROOT_GID as ROOT_GID,
    ROOT_UID as ROOT_UID,
    SUPPORTED_KEY_ALGORITHMS as SUPPORTED_KEY_ALGORITHMS,
    UPDATE_ALLOWED_SIGNERS_PATH as UPDATE_ALLOWED_SIGNERS_PATH,
    UPDATE_TRUST_TARGET,
    InstallPlan as InstallPlan,
    PublicKey as PublicKey,
    SignerEntry as SignerEntry,
    SignerInstallRequest,
    TrustKeyError as TrustKeyError,
    fingerprint as fingerprint,
    fingerprints_match as fingerprints_match,
    parse_allowed_signers as parse_allowed_signers,
    parse_public_key as parse_public_key,
    plan_signer_install,
    render_allowed_signers as _render_allowed_signers,
)
from automation.install.checks import CheckResult, exit_code, render  # noqa: E402
from automation.install.trust_file import (  # noqa: E402
    ROOT_GIDS,
    ROOT_UIDS,
    TRUST_DIRECTORY_MODE as TRUST_DIRECTORY_MODE,
    RealFilesystem as RealFilesystem,
    TrustKeyFilesystem as TrustKeyFilesystem,
    VerificationRequest,
    apply_install as apply_install,
    read_existing as read_existing,
    verify_installed as _verify_installed,
)

_Command: TypeAlias = Literal["fingerprint", "install", "verify"]


def render_allowed_signers(entries: Sequence[SignerEntry]) -> str:
    return _render_allowed_signers(entries, UPDATE_TRUST_TARGET)


def plan_install(
    key_text: str,
    *,
    principal: str = DEFAULT_UPDATE_TRUST_PRINCIPAL,
    path: Path = UPDATE_ALLOWED_SIGNERS_PATH,
    namespaces: str = GIT_SIGNATURE_NAMESPACE,
    existing: str = "",
) -> InstallPlan:
    target = replace(UPDATE_TRUST_TARGET, path=path)
    return plan_signer_install(
        SignerInstallRequest(key_text, principal, target, namespaces, existing)
    )


def plan_group_install(
    key_text: str,
    *,
    principal: str,
    path: Path = MANAGED_SKILLS_ALLOWED_SIGNERS_PATH,
    namespaces: str = GROUP_SIGNATURE_NAMESPACES,
) -> InstallPlan:
    target = replace(GROUP_SKILL_TRUST_TARGET, path=path)
    return plan_signer_install(
        SignerInstallRequest(key_text, principal, target, namespaces)
    )


def verify_installed(
    path: Path,
    filesystem: TrustKeyFilesystem,
    *,
    expected_fingerprint: str | None = None,
    trusted_uids: frozenset[int] = ROOT_UIDS,
    trusted_gids: frozenset[int] = ROOT_GIDS,
) -> tuple[CheckResult, ...]:
    request = VerificationRequest(
        path=path,
        target=replace(UPDATE_TRUST_TARGET, path=path),
        expected_fingerprint=expected_fingerprint,
        trusted_uids=trusted_uids,
        trusted_gids=trusted_gids,
    )
    return _verify_installed(request, filesystem)


def verify_group_installed(
    path: Path,
    filesystem: TrustKeyFilesystem,
    *,
    expected_fingerprint: str | None = None,
    trusted_uids: frozenset[int] = ROOT_UIDS,
    trusted_gids: frozenset[int] = ROOT_GIDS,
) -> tuple[CheckResult, ...]:
    request = VerificationRequest(
        path=path,
        target=replace(GROUP_SKILL_TRUST_TARGET, path=path),
        expected_fingerprint=expected_fingerprint,
        trusted_uids=trusted_uids,
        trusted_gids=trusted_gids,
    )
    return _verify_installed(request, filesystem)


def _read_key_text(source: Path) -> str:
    try:
        return source.read_text(encoding="utf-8")
    except OSError as error:
        raise TrustKeyError(
            f"BUNDLED-KEY-MISSING: {source}를 읽을 수 없다. "
            + "설치기 번들의 공개키 경로를 --key로 지정한다"
        ) from error


class _Arguments(argparse.Namespace):
    command: _Command
    key: Path
    path: Path
    principal: str
    dry_run: bool
    add: bool
    expect_fingerprint: str | None

    def __init__(self) -> None:
        super().__init__()
        self.command = "verify"
        self.key = Path()
        self.path = UPDATE_ALLOWED_SIGNERS_PATH
        self.principal = DEFAULT_UPDATE_TRUST_PRINCIPAL
        self.dry_run = False
        self.add = False
        self.expect_fingerprint = None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trust_key_bootstrap",
        description=(
            "업데이트 신뢰키를 update-allowed-signers에 설치·검증한다. "
            "그룹 스킬 서명키는 설치기의 --group-roster 경로가 별도로 처리한다(D8)."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fingerprint_parser = subparsers.add_parser(
        "fingerprint",
        help="번들 공개키의 SHA256 지문 출력",
    )
    _ = fingerprint_parser.add_argument(
        "--key",
        type=Path,
        required=True,
        help="번들에 동봉된 공개키 파일",
    )

    install_parser = subparsers.add_parser(
        "install",
        help="신뢰키 설치 (root 필요)",
    )
    _ = install_parser.add_argument(
        "--key",
        type=Path,
        required=True,
        help="번들에 동봉된 공개키 파일",
    )
    _ = install_parser.add_argument(
        "--path",
        type=Path,
        default=UPDATE_ALLOWED_SIGNERS_PATH,
    )
    _ = install_parser.add_argument(
        "--principal",
        default=DEFAULT_UPDATE_TRUST_PRINCIPAL,
    )
    _ = install_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="계획만 출력하고 쓰지 않는다",
    )
    _ = install_parser.add_argument(
        "--add", action="store_true", help="기존 엔트리 보존 병합 — 신뢰키 회전 중첩 구간용"
    )
    _ = install_parser.add_argument(
        "--expect-fingerprint",
        default=None,
        help="공지된 지문(대역외 값)",
    )

    verify_parser = subparsers.add_parser(
        "verify",
        help="설치본의 소유·모드·지문 검증",
    )
    _ = verify_parser.add_argument(
        "--path",
        type=Path,
        default=UPDATE_ALLOWED_SIGNERS_PATH,
    )
    _ = verify_parser.add_argument(
        "--expect-fingerprint",
        default=None,
        help="공지된 지문(대역외 값)",
    )
    return parser


def _install(args: _Arguments, filesystem: TrustKeyFilesystem) -> int:
    plan = plan_install(
        _read_key_text(args.key),
        principal=args.principal,
        path=args.path,
        existing=read_existing(args.path, filesystem) if args.add else "",
    )
    expected = args.expect_fingerprint
    if expected is not None and not fingerprints_match(plan.fingerprint, expected):
        print(
            f"TRUST-KEY-FINGERPRINT-MISMATCH: 번들 {plan.fingerprint} != 공지 {expected}",
            file=sys.stderr,
        )
        return 1
    print(plan.describe())
    if args.dry_run:
        return 0
    apply_install(plan, filesystem)
    return _verify(args, filesystem)


def _verify(args: _Arguments, filesystem: TrustKeyFilesystem) -> int:
    results = verify_installed(
        args.path,
        filesystem,
        expected_fingerprint=args.expect_fingerprint,
    )
    print(render(results, verdict_label="TRUSTED"))
    return exit_code(results)


def main(argv: Sequence[str] | None = None) -> int:
    args = _Arguments()
    _ = _parser().parse_args(argv, namespace=args)
    filesystem = RealFilesystem()
    try:
        match args.command:
            case "fingerprint":
                print(fingerprint(parse_public_key(_read_key_text(args.key))))
                return 0
            case "install":
                return _install(args, filesystem)
            case "verify":
                return _verify(args, filesystem)
    except TrustKeyError as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError as error:
        print(
            f"TRUST-KEY-WRITE-FAILED: {error} — root 권한으로 실행했는지 확인한다",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
