"""Create synthetic documents at runtime; no document fixture body is stored in git."""
from __future__ import annotations

import argparse
from pathlib import Path


_SERVICE_ORDER = (
    "# 용역지시서\n\n"
    "## 기본 정보\n"
    "- 과업명: {{과업명}}\n"
    "- 수행업체: {{수행업체}}\n"
    "- 수행기간: {{수행기간}}\n\n"
    "## 과업 범위\n"
    "- 과업범위: {{과업범위}}\n\n"
    "## 산출물\n"
    "- 산출물: {{산출물}}\n"
)
_VENDOR_REASON = (
    "# 업체추천사유서\n\n"
    "## 추천 대상\n"
    "- 업체명: {{업체명}}\n"
    "- 사업명: {{사업명}}\n\n"
    "## 추천 사유\n"
    "해당 업체의 수행 적합성을 확인하여 추천 사유를 정리한다.\n\n"
    "## 선정 근거\n"
    "과업 이해도, 수행 역량, 일정 대응을 종합하여 선정 근거를 제시한다.\n"
)


def make(out: Path) -> tuple[Path, Path]:
    """Write two synthetic runtime-only examples under a caller-provided private directory."""
    _ = out.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = out.chmod(0o700)
    service = out / "synthetic-service-order.md"
    vendor = out / "synthetic-vendor-reason.md"
    _ = service.write_text(_SERVICE_ORDER, encoding="utf-8")
    _ = vendor.write_text(_VENDOR_REASON, encoding="utf-8")
    _ = service.chmod(0o600)
    _ = vendor.chmod(0o600)
    return service, vendor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="make_doctype_fixtures")
    _ = parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    output: object = vars(args).get("out")
    if not isinstance(output, str):
        raise ValueError("--out must be a path")
    service, vendor = make(Path(output))
    print(f"FIXTURE service={service.name} narrative={vendor.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
