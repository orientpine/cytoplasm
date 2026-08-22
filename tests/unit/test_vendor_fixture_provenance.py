"""벤더 경계 픽스처 상수가 실측 근거 없이 기본값으로 굳지 않았는가.

2026-08 실측: `test_mail_compose_form_verify.py` 의 `_form(method="send")` 가 그것이었다.
이 웹메일은 compose 내내 `method=""` 를 낸다(12초 폴링, 프로덕션 로그도 08-12·13·14 모두
`""`). 즉 스위트는 **사이트가 내지 않는 값**을 기준으로 초록이었고, 그동안 실제 발송은 전면
불가였다. 초록이 안전을 보장하지 않은 것이다.

그때 그 한 상수는 실측값으로 고쳤지만 같은 부류가 다른 벤더 경로에도 있는지는 보지 않았다.
이 검사가 그 질문을 벤더 경계 전체로 넓힌다 — 벤더가 보고하는 필드를 흉내 내는 기본값에는
근거 주석(`실측`/`measured`)이 붙어 있어야 한다. 우리가 지어낸 합성값(수신자 주소 등)은
대상이 아니다: 문제는 「지어낸 값」이 아니라 「사이트가 낸다고 가정한 값」이다.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_UNIT = _REPO / "tests" / "unit"

#: mailon 의 폼 프로브가 실제로 돌려주는 필드들. 이 이름을 흉내 내는 기본값이 검사 대상이다.
_VENDOR_REPORTED = frozenset({"method", "to", "cc", "bcc", "from", "status", "verified"})
_PROVENANCE = re.compile(r"실측|measured|프로덕션 로그|production log", re.IGNORECASE)
#: 근거는 둘 중 하나다 — 사이트에서 재본 것(실측·로그)이거나, 그 값을 쓰는 우리 코드의 위치다.
_PROVENANCE = re.compile(r"실측|measured|프로덕션 로그|production log|\.py:\d+", re.IGNORECASE)

def _vendor_boundary_modules() -> list[Path]:
    """벤더 `mailon` 패키지를 import 하는 단위 테스트 모듈."""
    modules = [
        path for path in sorted(_UNIT.glob("test_*.py"))
        if "mailon" in path.read_text(encoding="utf-8")
        and "skills" in path.read_text(encoding="utf-8")
    ]
    assert modules, "벤더 경계 테스트 모듈을 하나도 찾지 못했다 — 레이아웃이 바뀌었나"
    return modules


def _parameter_name_matches_vendor_field(name: str) -> bool:
    return name in _VENDOR_REPORTED or name.removesuffix("_value") in _VENDOR_REPORTED


def _unannotated_vendor_defaults(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    tree = ast.parse("\n".join(lines))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body_start = node.body[0].lineno if node.body else node.lineno
        signature = "\n".join(lines[node.lineno - 1 : body_start - 1])
        if _PROVENANCE.search(signature):
            continue
        arguments = node.args
        positional = arguments.posonlyargs + arguments.args
        pairs = list(
            zip(positional[len(positional) - len(arguments.defaults):], arguments.defaults, strict=True)
        ) + list(zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True))
        for argument, default in pairs:
            if not _parameter_name_matches_vendor_field(argument.arg):
                continue
            if isinstance(default, ast.Constant) and isinstance(default.value, str):
                offenders.append(f"{node.name}({argument.arg}={default.value!r}) @ line {node.lineno}")
    return offenders


@pytest.mark.parametrize(
    "module", _vendor_boundary_modules(), ids=lambda path: path.name
)
def test_vendor_reported_defaults_carry_their_measurement(module: Path) -> None:
    # Then: any default standing in for a value the site produces must say where that
    # value was measured — otherwise a green suite proves nothing about the site.
    offenders = _unannotated_vendor_defaults(module)

    assert not offenders, (
        f"{module.name}: 벤더가 낸다고 가정한 기본값에 실측 근거가 없다. 주석으로 근거(로그·"
        f"프로브·측정일)를 달거나 node 기반 실제-JS 검사로 대체한다: {offenders}"
    )


def test_the_detector_actually_catches_the_2026_08_regression(tmp_path: Path) -> None:
    # Given: the exact shape that let the suite go green while every send failed.
    regression = tmp_path / "test_regression.py"
    regression.write_text(
        "import mailon  # skills/mail/vendor\n"
        "def _form(to_value: str, *, method: str = 'send') -> dict:\n"
        "    return {'method': method, 'to': to_value}\n",
        encoding="utf-8",
    )

    # Then: it is reported, and adding the measurement clears it.
    assert _unannotated_vendor_defaults(regression)
    regression.write_text(
        "import mailon  # skills/mail/vendor\n"
        "def _form(\n"
        "    to_value: str,\n"
        "    # 실측 2026-08-18: 이 웹메일은 compose 내내 method=\"\" 를 낸다.\n"
        "    method: str = '',\n"
        ") -> dict:\n"
        "    return {'method': method, 'to': to_value}\n",
        encoding="utf-8",
    )
    assert not _unannotated_vendor_defaults(regression)
