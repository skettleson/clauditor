from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .audit import audit
from .collector import DEFAULT_STATE_FILE, collect
from .mcp import LocalSource, RemoteSource, serve_stdio
from .model import Verdict
from .policy import PolicyError, load_policy
from .report import render
from .server import ServerConfig, make_server
from .transcripts import DEFAULT_PROJECTS_ROOT, load_session, load_sessions

INGEST_TOKEN_ENV = "CLAUDITOR_INGEST_TOKEN"
READ_TOKEN_ENV = "CLAUDITOR_READ_TOKEN"
SERVER_ENV = "CLAUDITOR_SERVER"

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_POLICY_DIR = PACKAGE_DIR.parent / "policy"
FIXTURES_DIR = PACKAGE_DIR.parent / "fixtures"
DEMO_CASES = (
    ("coding_session.jsonl", "software-engineer"),
    ("coding_session.jsonl", "support-analyst"),
    ("pentest_session.jsonl", "software-engineer"),
    ("pentest_session.jsonl", "security-engineer"),
)


class UsageError(ValueError):
    pass


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
    collect_cmd = commands.add_parser("collect")
    collect_cmd.add_argument("--server", default=os.environ.get(SERVER_ENV))
    collect_cmd.add_argument("--root", type=Path, default=DEFAULT_PROJECTS_ROOT)
    collect_cmd.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8750)
    serve.add_argument("--db", type=Path, default=Path("clauditor.db"))
    serve.add_argument("--no-auth", action="store_true")
    mcp = commands.add_parser("mcp")
    mcp.add_argument("--server", default=os.environ.get(SERVER_ENV))
    mcp.add_argument("--root", type=Path, default=DEFAULT_PROJECTS_ROOT)
    args = parser.parse_args(argv)
    try:
        match args.command:
            case "demo":
                return run_demo(args.policy_dir)
            case "collect":
                return run_collect(args.server, args.root, args.state_file)
            case "serve":
                return run_serve(args.host, args.port, args.db, args.policy_dir, args.no_auth)
            case "mcp":
                return run_mcp(args.server, args.root, args.policy_dir)
            case _:
                return run_scan(args.root, args.policy_dir, args.role, args.only, args.format, args.show_aligned, args.fail_on_drift)
    except PolicyError as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2
    except UsageError as error:
        print(f"clauditor: {error}", file=sys.stderr)
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


def run_collect(server: str | None, root: Path, state_file: Path) -> int:
    if not server:
        raise UsageError(f"collect needs --server or {SERVER_ENV}")
    result = collect(server, os.environ.get(INGEST_TOKEN_ENV), root, state_file)
    print(f"shipped {result.shipped}, unchanged {result.unchanged}, failed {result.failed}")
    if result.last_error:
        print(f"clauditor: last ship error: {result.last_error}", file=sys.stderr)
    return 1 if result.failed else 0


def run_serve(host: str, port: int, db: Path, policy_dir: Path, no_auth: bool) -> int:
    ingest_token, read_token = os.environ.get(INGEST_TOKEN_ENV), os.environ.get(READ_TOKEN_ENV)
    if not no_auth and not (ingest_token and read_token):
        raise UsageError(f"serve needs {INGEST_TOKEN_ENV} and {READ_TOKEN_ENV}, or --no-auth for local testing")
    if ingest_token and ingest_token == read_token:
        raise UsageError(f"{INGEST_TOKEN_ENV} and {READ_TOKEN_ENV} must differ, or every collector can read every audit")
    server = make_server(host, port, ServerConfig(db, _policy(policy_dir), ingest_token, read_token))
    print(f"clauditor serving on http://{host}:{server.server_port} db {db}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def run_mcp(server: str | None, root: Path, policy_dir: Path) -> int:
    serve_stdio(RemoteSource(server, os.environ.get(READ_TOKEN_ENV)) if server else LocalSource(root, _policy(policy_dir)))
    return 0


def _policy(policy_dir: Path):
    return load_policy(policy_dir / "capabilities.toml", policy_dir / "roles.toml")


if __name__ == "__main__":
    sys.exit(main())
