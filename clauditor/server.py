from __future__ import annotations

import hmac
import json
from collections.abc import Iterable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .audit import audit
from .model import Policy, Session
from .report import ReportFormat, render, render_roles
from .store import Store
from .wire import decode_batch

MAX_BODY_BYTES = 64 * 1024 * 1024
CONTENT_TYPES: dict[str, str] = {
    "json": "application/json",
    "text": "text/plain; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
}


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
        if fmt not in CONTENT_TYPES:
            raise QueryError(f"format must be one of {sorted(CONTENT_TYPES)}")
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


@dataclass(frozen=True, slots=True)
class ServerConfig:
    db_path: Path
    policy: Policy
    ingest_token: str | None
    read_token: str | None


def make_server(host: str, port: int, config: ServerConfig) -> ThreadingHTTPServer:
    Store(config.db_path).close()
    return ThreadingHTTPServer((host, port), type("Handler", (_Handler,), {"config": config}))


class _Handler(BaseHTTPRequestHandler):
    config: ServerConfig
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        match url.path:
            case "/healthz":
                self._send(HTTPStatus.OK, "ok\n")
            case "/v1/roles":
                if self._authorized(self.config.read_token):
                    self._send(HTTPStatus.OK, render_roles(self.config.policy), CONTENT_TYPES["json"])
            case "/v1/audit":
                if self._authorized(self.config.read_token):
                    self._audit({k: v[-1] for k, v in parse_qs(url.query).items()})
            case _:
                self._send(HTTPStatus.NOT_FOUND, "not found\n")

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/v1/ingest":
            self._reject(HTTPStatus.NOT_FOUND, "not found\n")
            return
        if not self._authorized(self.config.ingest_token):
            return
        length = self.headers.get("content-length", "")
        if not length.isdigit():
            self._reject(HTTPStatus.LENGTH_REQUIRED, "content-length required\n")
            return
        if int(length) > MAX_BODY_BYTES:
            self._reject(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"body over {MAX_BODY_BYTES} bytes\n")
            return
        try:
            sessions = decode_batch(json.loads(self.rfile.read(int(length))))
        except ValueError as error:
            self._send(HTTPStatus.BAD_REQUEST, f"{error}\n")
            return
        store = Store(self.config.db_path)
        try:
            stored = store.put(sessions)
        finally:
            store.close()
        self._send(HTTPStatus.OK, json.dumps({"stored": stored}), CONTENT_TYPES["json"])

    def _audit(self, raw: dict[str, str]) -> None:
        try:
            query = AuditQuery.parse(raw, self.config.policy)
        except QueryError as error:
            self._send(HTTPStatus.BAD_REQUEST, f"{error}\n")
            return
        store = Store(self.config.db_path)
        try:
            sessions = store.sessions(query.user, query.since, query.session)
        finally:
            store.close()
        self._send(HTTPStatus.OK, report(sessions, self.config.policy, query), CONTENT_TYPES[query.fmt])

    def _authorized(self, token: str | None) -> bool:
        if token is None:
            return True
        presented = self.headers.get("authorization", "").removeprefix("Bearer ")
        if hmac.compare_digest(presented.encode(), token.encode()):
            return True
        self._reject(HTTPStatus.UNAUTHORIZED, "unauthorized\n")
        return False

    def _reject(self, status: HTTPStatus, body: str) -> None:
        self.close_connection = True
        self._send(status, body)

    def _send(self, status: HTTPStatus, body: str, content_type: str = CONTENT_TYPES["text"]) -> None:
        payload = body.encode()
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(payload)))
        if self.close_connection:
            self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(payload)


def _optional(raw: dict[str, str | bool | None], field: str) -> str | None:
    value = raw.get(field)
    return value if isinstance(value, str) and value else None
