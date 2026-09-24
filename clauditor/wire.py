from __future__ import annotations

from pathlib import PurePosixPath

from .model import Activity, ActivityKind, EvidenceRef, Owner, Session

SCHEMA_VERSION = 1
MAX_SUBJECT_CHARS = 20_000


class WireError(ValueError):
    pass


def encode_batch(owner: Owner, sessions: list[Session]) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "owner": {"host": owner.host, "local_user": owner.local_user, "email": owner.email},
        "sessions": [encode_session(session) for session in sessions],
    }


def encode_session(session: Session) -> dict:
    return {
        "session_id": session.session_id,
        "source": session.source,
        "project_cwd": session.project_cwd,
        "title": session.title,
        "started_at": session.started_at,
        "activities": [
            {
                "kind": a.kind.value,
                "tool_name": a.tool_name,
                "subject": a.subject[:MAX_SUBJECT_CHARS],
                "transcript": str(a.evidence.transcript),
                "line_no": a.evidence.line_no,
                "event_uuid": a.evidence.event_uuid,
                "timestamp": a.evidence.timestamp,
                "subagent_id": a.evidence.subagent_id,
            }
            for a in session.activities
        ],
    }


def decode_batch(raw: object) -> list[Session]:
    body = _object(raw, "batch")
    if body.get("schema") != SCHEMA_VERSION:
        raise WireError(f"unsupported schema {body.get('schema')!r}, expected {SCHEMA_VERSION}")
    owner_raw = _object(body.get("owner"), "owner")
    owner = Owner(_text(owner_raw, "host"), _text(owner_raw, "local_user"), _optional_text(owner_raw, "email"))
    sessions = body.get("sessions")
    if not isinstance(sessions, list):
        raise WireError("sessions must be a list")
    return [_decode_session(_object(s, "session"), owner) for s in sessions]


def _decode_session(raw: dict, owner: Owner) -> Session:
    activities = raw.get("activities")
    if not isinstance(activities, list):
        raise WireError("activities must be a list")
    return Session(
        session_id=_text(raw, "session_id"),
        project_cwd=_text(raw, "project_cwd", allow_empty=True),
        title=_optional_text(raw, "title"),
        started_at=_text(raw, "started_at", allow_empty=True),
        activities=tuple(_decode_activity(_object(a, "activity")) for a in activities),
        source=_text(raw, "source"),
        owner=owner,
    )


def _decode_activity(raw: dict) -> Activity:
    try:
        kind = ActivityKind(raw.get("kind"))
    except ValueError as error:
        raise WireError(str(error)) from error
    line_no = raw.get("line_no")
    if not isinstance(line_no, int) or line_no < 1:
        raise WireError(f"line_no must be a positive int, got {line_no!r}")
    return Activity(
        kind=kind,
        tool_name=_optional_text(raw, "tool_name"),
        subject=_text(raw, "subject", allow_empty=True)[:MAX_SUBJECT_CHARS],
        evidence=EvidenceRef(
            transcript=PurePosixPath(_text(raw, "transcript")),
            line_no=line_no,
            event_uuid=_text(raw, "event_uuid", allow_empty=True),
            timestamp=_text(raw, "timestamp", allow_empty=True),
            subagent_id=_optional_text(raw, "subagent_id"),
        ),
    )


def _object(value: object, what: str) -> dict:
    if not isinstance(value, dict):
        raise WireError(f"{what} must be an object")
    return value


def _text(raw: dict, field: str, allow_empty: bool = False) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or (not value and not allow_empty):
        raise WireError(f"{field} must be a{'' if allow_empty else ' non-empty'} string")
    return value


def _optional_text(raw: dict, field: str) -> str | None:
    value = raw.get(field)
    if value is None or isinstance(value, str):
        return value or None
    raise WireError(f"{field} must be a string or null")
