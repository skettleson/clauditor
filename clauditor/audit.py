from __future__ import annotations

from collections.abc import Iterable

from .model import Activity, Capability, Finding, Policy, Role, Session, SessionAudit, Verdict


def audit(sessions: Iterable[Session], policy: Policy, role_override: str | None = None) -> list[SessionAudit]:
    raise NotImplementedError


def audit_session(session: Session, role: Role | None, capabilities: tuple[Capability, ...]) -> SessionAudit:
    raise NotImplementedError


def classify(activity: Activity, capabilities: tuple[Capability, ...]) -> list[tuple[Capability, str]]:
    raise NotImplementedError


def judge(findings: tuple[Finding, ...], role: Role) -> tuple[Verdict, int]:
    raise NotImplementedError
