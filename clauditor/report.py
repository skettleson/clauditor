from __future__ import annotations

import json
from collections import Counter
from typing import Literal

from .model import Finding, Policy, SessionAudit, Stance, Verdict

ReportFormat = Literal["text", "markdown", "json"]

VERDICT_ORDER = {Verdict.DRIFTED: 0, Verdict.REVIEW: 1, Verdict.UNASSIGNED: 2, Verdict.ALIGNED: 3}
SUBJECT_LIMIT = 120


def render(audits: list[SessionAudit], fmt: ReportFormat = "text", show_aligned: bool = False) -> str:
    ordered = sorted(audits, key=lambda a: (VERDICT_ORDER[a.verdict], -a.drift_points, a.session.started_at))
    shown = [a for a in ordered if show_aligned or a.verdict is not Verdict.ALIGNED]
    match fmt:
        case "json":
            return json.dumps({"sessions": [_as_json(a) for a in shown], "summary": _counts(audits)}, indent=2)
        case "markdown":
            return "\n".join(["# clauditor report", "", *(_markdown(a) for a in shown), _footer(audits)])
        case _:
            return "\n".join([*(_text(a) for a in shown), _footer(audits)])


def render_roles(policy: Policy) -> str:
    roles = {
        role.name: {"description": role.description, "expected": sorted(role.expected), "forbidden": sorted(role.forbidden), "drift_threshold": role.drift_threshold}
        for role in policy.roles.values()
    }
    return json.dumps({"default_role": policy.default_role, "roles": roles, "users": policy.users}, indent=2)


def _text(audit: SessionAudit) -> str:
    lines = [_headline(audit), f"  project  {audit.session.project_cwd}   {audit.session.started_at}   {len(audit.session.activities)} activities, {audit.unclassified_count} unclassified"]
    for finding in audit.violations:
        lines.append(f"  FORBIDDEN {finding.capability.name} ({finding.capability.severity.name.lower()})  {finding.activity.kind.value}  {_subject(finding)}")
        lines.append(f"            {_cite(finding)}")
    lines.append(f"  in-role: {_in_role_summary(audit)}")
    return "\n".join(lines) + "\n"


def _markdown(audit: SessionAudit) -> str:
    lines = [f"## {audit.verdict.value.upper()}: {audit.session.title or audit.session.session_id}", "", f"- {audit.session.source} session `{audit.session.session_id}`, role `{_role_name(audit)}`, drift {audit.drift_points}", f"- project `{audit.session.project_cwd}`, started {audit.session.started_at}", f"- in-role: {_in_role_summary(audit)}", ""]
    if audit.violations:
        lines += ["| capability | severity | activity | evidence |", "|---|---|---|---|"]
        lines += [f"| {f.capability.name} | {f.capability.severity.name.lower()} | `{_subject(f).replace('|', '\\|')}` | `{_cite(f)}` |" for f in audit.violations]
        lines.append("")
    return "\n".join(lines)


def _as_json(audit: SessionAudit) -> dict:
    return {
        "session_id": audit.session.session_id,
        "source": audit.session.source,
        "title": audit.session.title,
        "project": audit.session.project_cwd,
        "started_at": audit.session.started_at,
        "role": _role_name(audit),
        "verdict": audit.verdict.value,
        "drift_points": audit.drift_points,
        "unclassified_count": audit.unclassified_count,
        "violations": [
            {
                "capability": f.capability.name,
                "severity": f.capability.severity.name.lower(),
                "kind": f.activity.kind.value,
                "subject": f.activity.subject,
                "matched": f.matched_text,
                "transcript": str(f.activity.evidence.transcript),
                "line": f.activity.evidence.line_no,
                "uuid": f.activity.evidence.event_uuid,
                "subagent": f.activity.evidence.subagent_id,
            }
            for f in audit.violations
        ],
    }


def _headline(audit: SessionAudit) -> str:
    title = f'"{audit.session.title}"' if audit.session.title else ""
    return f"{audit.verdict.value.upper():<10} drift {audit.drift_points:<3} {_role_name(audit)}  {title}  {audit.session.source} {audit.session.session_id[:8]}"


def _role_name(audit: SessionAudit) -> str:
    return audit.role.name if audit.role else "(none)"


def _subject(finding: Finding) -> str:
    flat = " ".join(finding.activity.subject.split())
    if len(flat) <= SUBJECT_LIMIT:
        return flat
    start = max(0, min(flat.find(finding.matched_text) - SUBJECT_LIMIT // 3, len(flat) - SUBJECT_LIMIT))
    excerpt = flat[start : start + SUBJECT_LIMIT]
    return ("…" if start else "") + excerpt + ("…" if start + SUBJECT_LIMIT < len(flat) else "")


def _cite(finding: Finding) -> str:
    evidence = finding.activity.evidence
    subagent = f"  subagent {evidence.subagent_id}" if evidence.subagent_id else ""
    uuid = f" uuid {evidence.event_uuid[:8]}" if evidence.event_uuid else ""
    return f"{evidence.transcript.name}:{evidence.line_no}{uuid}{subagent}"


def _in_role_summary(audit: SessionAudit) -> str:
    counts = Counter(f.capability.name for f in audit.findings if f.stance is Stance.EXPECTED)
    return ", ".join(f"{name} x{n}" for name, n in counts.most_common()) or "nothing expected matched"


def _counts(audits: list[SessionAudit]) -> dict[str, int]:
    counts = Counter(a.verdict.value for a in audits)
    return {"sessions": len(audits)} | {verdict.value: counts.get(verdict.value, 0) for verdict in VERDICT_ORDER}


def _footer(audits: list[SessionAudit]) -> str:
    c = _counts(audits)
    return f"{c['sessions']} sessions: {c['drifted']} drifted, {c['review']} review, {c['aligned']} aligned, {c['unassigned']} unassigned"
