from __future__ import annotations

from pathlib import Path

from .model import Policy


class PolicyError(ValueError):
    pass


def load_policy(capabilities_file: Path, roles_file: Path) -> Policy:
    raise NotImplementedError


def _parse_capabilities(raw: dict, source: Path) -> tuple:
    raise NotImplementedError


def _parse_roles(raw: dict, known_capabilities: frozenset[str], source: Path) -> tuple:
    raise NotImplementedError
