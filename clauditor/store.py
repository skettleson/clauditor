from __future__ import annotations

import sqlite3
from pathlib import Path, PurePosixPath

from .model import Activity, ActivityKind, EvidenceRef, Owner, Session

SCHEMA = """
create table if not exists sessions (
    host text not null,
    source text not null,
    session_id text not null,
    local_user text not null,
    email text,
    project_cwd text not null,
    title text,
    started_at text not null,
    received_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    primary key (host, source, session_id)
);
create table if not exists activities (
    host text not null,
    source text not null,
    session_id text not null,
    seq integer not null,
    kind text not null,
    tool_name text,
    subject text not null,
    transcript text not null,
    line_no integer not null,
    event_uuid text not null,
    timestamp text not null,
    subagent_id text,
    primary key (host, source, session_id, seq),
    foreign key (host, source, session_id) references sessions on delete cascade
);
create index if not exists sessions_by_email on sessions (email);
create index if not exists sessions_by_local_user on sessions (local_user);
"""


class Store:
    def __init__(self, path: Path) -> None:
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("pragma journal_mode = wal")
        self._db.execute("pragma foreign_keys = on")
        self._db.execute("pragma busy_timeout = 5000")
        self._db.executescript(SCHEMA)

    def put(self, sessions: list[Session]) -> int:
        with _transaction(self._db):
            for session in sessions:
                self._replace(session)
        return len(sessions)

    def sessions(self, user: str | None = None, since: str | None = None, session_prefix: str | None = None) -> list[Session]:
        clauses, params = [], []
        if user:
            clauses.append("(lower(email) = lower(?) or lower(local_user) = lower(?))")
            params += [user, user]
        if since:
            clauses.append("started_at >= ?")
            params.append(since)
        if session_prefix:
            clauses.append("session_id like ? || '%'")
            params.append(session_prefix)
        where = f"where {' and '.join(clauses)}" if clauses else ""
        rows = self._db.execute(f"select host, source, session_id, local_user, email, project_cwd, title, started_at from sessions {where} order by started_at", params).fetchall()
        return [self._session(*row) for row in rows]

    def close(self) -> None:
        self._db.close()

    def _replace(self, session: Session) -> None:
        owner = session.owner or Owner("unknown", "unknown", None)
        key = (owner.host, session.source, session.session_id)
        self._db.execute("delete from sessions where host = ? and source = ? and session_id = ?", key)
        self._db.execute(
            "insert into sessions (host, source, session_id, local_user, email, project_cwd, title, started_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
            (*key, owner.local_user, owner.email, session.project_cwd, session.title, session.started_at),
        )
        self._db.executemany(
            "insert into activities values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (*key, seq, a.kind.value, a.tool_name, a.subject, str(a.evidence.transcript), a.evidence.line_no, a.evidence.event_uuid, a.evidence.timestamp, a.evidence.subagent_id)
                for seq, a in enumerate(session.activities)
            ],
        )

    def _session(self, host: str, source: str, session_id: str, local_user: str, email: str | None, project_cwd: str, title: str | None, started_at: str) -> Session:
        rows = self._db.execute(
            "select kind, tool_name, subject, transcript, line_no, event_uuid, timestamp, subagent_id from activities where host = ? and source = ? and session_id = ? order by seq",
            (host, source, session_id),
        )
        activities = tuple(
            Activity(ActivityKind(kind), tool_name, subject, EvidenceRef(PurePosixPath(transcript), line_no, event_uuid, timestamp, subagent_id))
            for kind, tool_name, subject, transcript, line_no, event_uuid, timestamp, subagent_id in rows
        )
        return Session(session_id, project_cwd, title, started_at, activities, source, Owner(host, local_user, email))


class _transaction:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def __enter__(self) -> None:
        self._db.execute("begin immediate")

    def __exit__(self, error_type, error, traceback) -> None:
        self._db.execute("rollback" if error_type else "commit")
