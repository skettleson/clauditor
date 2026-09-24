from __future__ import annotations

import re
import tomllib
from pathlib import Path

from .model import ActivityKind, Assignment, Capability, Matcher, Policy, Role, Severity


class PolicyError(ValueError):
    pass


COMMAND_POSITION = r"(?:^|[;&|(`]\s*|\$\(\s*|\bsudo\s+|\bxargs\s+|\bexec\s+)"


def load_policy(capabilities_file: Path, roles_file: Path) -> Policy:
    capabilities = _parse_capabilities(_read_toml(capabilities_file), capabilities_file)
    known = frozenset(capability.name for capability in capabilities)
    raw_roles = _read_toml(roles_file)
    roles = _parse_roles(raw_roles, known, roles_file)
    assignments = tuple(Assignment(entry["project"], entry["role"]) for entry in raw_roles.get("assign", []))
    default_role = raw_roles.get("default_role")
    users = {identity.lower(): role for identity, role in raw_roles.get("users", {}).items()}
    for role_name in [a.role_name for a in assignments] + list(users.values()) + ([default_role] if default_role else []):
        if role_name not in roles:
            raise PolicyError(f"{roles_file}: assignment or user names unknown role {role_name!r}")
    return Policy(capabilities, roles, assignments, default_role, users)


def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PolicyError(f"{path}: {error}") from error


def _parse_capabilities(raw: dict, source: Path) -> tuple[Capability, ...]:
    return tuple(_parse_capability(name, body, source) for name, body in raw.items())


def _parse_capability(name: str, body: dict, source: Path) -> Capability:
    try:
        severity = Severity[body["severity"].upper()]
        matchers = tuple(
            Matcher(frozenset(ActivityKind(kind) for kind in entry["kinds"]), re.compile(_expand(entry["regex"]), re.IGNORECASE | re.MULTILINE))
            for entry in body["match"]
        )
    except (KeyError, ValueError, re.error) as error:
        raise PolicyError(f"{source}: capability {name!r}: {error}") from error
    return Capability(name, body.get("description", ""), severity, matchers)


def _expand(regex: str) -> str:
    return regex.replace("{cmd}", COMMAND_POSITION)


def _parse_roles(raw: dict, known_capabilities: frozenset[str], source: Path) -> dict[str, Role]:
    roles = {}
    for name, body in raw.get("roles", {}).items():
        expected = frozenset(body.get("expected", []))
        forbidden = frozenset(body.get("forbidden", []))
        unknown = sorted((expected | forbidden) - known_capabilities)
        if unknown:
            raise PolicyError(f"{source}: role {name!r} names unknown capabilities {unknown}")
        overlap = sorted(expected & forbidden)
        if overlap:
            raise PolicyError(f"{source}: role {name!r} both expects and forbids {overlap}")
        roles[name] = Role(name, body.get("description", ""), expected, forbidden, int(body.get("drift_threshold", 10)))
    return roles
