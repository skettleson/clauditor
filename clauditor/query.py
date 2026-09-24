from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import get_args

from .audit import audit
from .model import Policy, Session
from .report import ReportFormat, render

REPORT_FORMATS = get_args(ReportFormat)


class QueryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AuditQuery:
    user: str | None = None
    since: str | None = None
    session: str | None = None
    role: str | None = None
    fmt: ReportFormat = "json"
    show_aligned: bool = False

    @classmethod
    def parse(cls, raw: dict[str, str | bool | None], policy: Policy) -> AuditQuery:
        fmt = raw.get("format") or "json"
        if fmt not in REPORT_FORMATS:
            raise QueryError(f"format must be one of {sorted(REPORT_FORMATS)}")
        role = raw.get("role") or None
        if role is not None and role not in policy.roles:
            raise QueryError(f"unknown role {role!r}, known: {sorted(policy.roles)}")
        return cls(
            user=_optional(raw, "user"),
            since=_optional(raw, "since"),
            session=_optional(raw, "session"),
            role=role,
            fmt=fmt,
            show_aligned=raw.get("show_aligned") in (True, "1", "true"),
        )

    def as_params(self) -> dict[str, str]:
        params = {"user": self.user, "since": self.since, "session": self.session, "role": self.role, "format": self.fmt}
        return {k: v for k, v in params.items() if v} | ({"show_aligned": "1"} if self.show_aligned else {})


def report(sessions: Iterable[Session], policy: Policy, query: AuditQuery) -> str:
    return render(audit(sessions, policy, query.role), query.fmt, query.show_aligned)


def _optional(raw: dict[str, str | bool | None], field: str) -> str | None:
    value = raw.get(field)
    return value if isinstance(value, str) and value else None
