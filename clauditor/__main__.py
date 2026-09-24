from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .audit import audit
from .model import Verdict
from .policy import PolicyError, load_policy
from .report import render
from .transcripts import DEFAULT_PROJECTS_ROOT, load_session, load_sessions

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_POLICY_DIR = PACKAGE_DIR.parent / "policy"
FIXTURES_DIR = PACKAGE_DIR.parent / "fixtures"
DEMO_CASES = (
    ("coding_session.jsonl", "software-engineer"),
    ("coding_session.jsonl", "support-analyst"),
    ("pentest_session.jsonl", "software-engineer"),
    ("pentest_session.jsonl", "security-engineer"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clauditor", description="Audit Claude Code sessions against the operator's job function.")
    parser.add_argument("--policy-dir", type=Path, default=DEFAULT_POLICY_DIR)
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan")
    scan.add_argument("--root", type=Path, default=DEFAULT_PROJECTS_ROOT)
    scan.add_argument("--role")
    scan.add_argument("--only")
    scan.add_argument("--format", choices=["text", "markdown", "json"], default="text")
    scan.add_argument("--show-aligned", action="store_true")
    scan.add_argument("--fail-on-drift", action="store_true")
    commands.add_parser("demo")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            return run_demo(args.policy_dir)
        return run_scan(args.root, args.policy_dir, args.role, args.only, args.format, args.show_aligned, args.fail_on_drift)
    except PolicyError as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2


def run_scan(root: Path, policy_dir: Path, role_override: str | None, only: str | None, fmt: str, show_aligned: bool, fail_on_drift: bool) -> int:
    policy = _policy(policy_dir)
    if role_override and role_override not in policy.roles:
        raise PolicyError(f"unknown role {role_override!r}, known: {sorted(policy.roles)}")
    audits = audit(load_sessions(root, only), policy, role_override)
    print(render(audits, fmt, show_aligned))
    return 1 if fail_on_drift and any(a.verdict is Verdict.DRIFTED for a in audits) else 0


def run_demo(policy_dir: Path = DEFAULT_POLICY_DIR) -> int:
    policy = _policy(policy_dir)
    for fixture, role in DEMO_CASES:
        path = FIXTURES_DIR / fixture
        if not path.exists():
            print(f"=== {fixture} as {role}: fixture missing, skipped\n")
            continue
        print(f"=== {fixture} as {role}")
        print(render(audit([load_session(path)], policy, role), show_aligned=True))
    return 0


def _policy(policy_dir: Path):
    return load_policy(policy_dir / "capabilities.toml", policy_dir / "roles.toml")


if __name__ == "__main__":
    sys.exit(main())
