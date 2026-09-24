from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from fnmatch import fnmatch
from pathlib import Path
import re


class ActivityKind(str, Enum):
    PROMPT = "prompt"
    SHELL = "shell"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    WEB = "web"
    MCP = "mcp"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    transcript: Path
    line_no: int
    event_uuid: str
    timestamp: str
    subagent_id: str | None


@dataclass(frozen=True, slots=True)
class Activity:
    kind: ActivityKind
    tool_name: str | None
    subject: str
    evidence: EvidenceRef


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    project_cwd: str
    title: str | None
    started_at: str
    activities: tuple[Activity, ...]


class Severity(IntEnum):
    LOW = 1
    MEDIUM = 3
    HIGH = 10


@dataclass(frozen=True, slots=True)
class Matcher:
    kinds: frozenset[ActivityKind]
    pattern: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    description: str
    severity: Severity
    matchers: tuple[Matcher, ...]


class Stance(str, Enum):
    EXPECTED = "expected"
    TOLERATED = "tolerated"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class Role:
    name: str
    description: str
    expected: frozenset[str]
    forbidden: frozenset[str]
    drift_threshold: int

    def stance_toward(self, capability_name: str) -> Stance:
        if capability_name in self.forbidden:
            return Stance.FORBIDDEN
        if capability_name in self.expected:
            return Stance.EXPECTED
        return Stance.TOLERATED


@dataclass(frozen=True, slots=True)
class Assignment:
    project_glob: str
    role_name: str


@dataclass(frozen=True, slots=True)
class Policy:
    capabilities: tuple[Capability, ...]
    roles: dict[str, Role]
    assignments: tuple[Assignment, ...]
    default_role: str | None

    def role_for(self, session: Session) -> Role | None:
        for assignment in self.assignments:
            if fnmatch(session.project_cwd, assignment.project_glob):
                return self.roles[assignment.role_name]
        return self.roles.get(self.default_role) if self.default_role else None


@dataclass(frozen=True, slots=True)
class Finding:
    activity: Activity
    capability: Capability
    stance: Stance
    matched_text: str


class Verdict(str, Enum):
    ALIGNED = "aligned"
    REVIEW = "review"
    DRIFTED = "drifted"
    UNASSIGNED = "unassigned"


@dataclass(frozen=True, slots=True)
class SessionAudit:
    session: Session
    role: Role | None
    verdict: Verdict
    drift_points: int
    findings: tuple[Finding, ...]
    unclassified_count: int

    @property
    def violations(self) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if finding.stance is Stance.FORBIDDEN)
