from __future__ import annotations

from collections.abc import Iterable

from .model import Activity, Capability, Finding, Policy, Role, Session, SessionAudit, Stance, Verdict


def audit(sessions: Iterable[Session], policy: Policy, role_override: str | None = None) -> list[SessionAudit]:
    override = policy.roles[role_override] if role_override else None
    return [audit_session(session, override or policy.role_for(session), policy.capabilities) for session in sessions]


def audit_session(session: Session, role: Role | None, capabilities: tuple[Capability, ...]) -> SessionAudit:
    findings: list[Finding] = []
    unclassified_count = 0
    for activity in session.activities:
        matches = classify(activity, capabilities)
        if not matches:
            unclassified_count += 1
        stance_of = role.stance_toward if role else lambda _: Stance.TOLERATED
        findings.extend(Finding(activity, capability, stance_of(capability.name), text) for capability, text in matches)
    verdict, drift_points = judge(tuple(findings), role) if role else (Verdict.UNASSIGNED, 0)
    return SessionAudit(session, role, verdict, drift_points, tuple(findings), unclassified_count)


def classify(activity: Activity, capabilities: tuple[Capability, ...]) -> list[tuple[Capability, str]]:
    matches = []
    for capability in capabilities:
        for matcher in capability.matchers:
            if activity.kind in matcher.kinds and (hit := matcher.pattern.search(activity.subject)):
                matches.append((capability, hit.group(0).strip()))
                break
    return matches


def judge(findings: tuple[Finding, ...], role: Role) -> tuple[Verdict, int]:
    forbidden = {finding.capability.name: finding.capability.severity for finding in findings if finding.stance is Stance.FORBIDDEN}
    drift_points = sum(forbidden.values())
    if drift_points >= role.drift_threshold:
        return Verdict.DRIFTED, drift_points
    if drift_points > 0:
        return Verdict.REVIEW, drift_points
    return Verdict.ALIGNED, drift_points
