from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_POLICY_DIR = PACKAGE_DIR.parent / "policy"
FIXTURES_DIR = PACKAGE_DIR.parent / "fixtures"


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError


def run_scan(root: Path, policy_dir: Path, role_override: str | None, fmt: str, show_aligned: bool, fail_on_drift: bool) -> int:
    raise NotImplementedError


def run_demo() -> int:
    raise NotImplementedError


if __name__ == "__main__":
    sys.exit(main())
