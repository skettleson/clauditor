from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .mcp import SourceError, respond
from .model import Policy
from .query import AuditQuery, QueryError, report
from .report import render_roles
from .store import Store
from .wire import decode_batch

MAX_BODY_BYTES = 64 * 1024 * 1024
CONTENT_TYPES: dict[str, str] = {
    "json": "application/json",
    "text": "text/plain; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
}


@dataclass(frozen=True, slots=True)
class ServerConfig:
    db_path: Path
    policy: Policy
    ingest_token: str | None
    read_token: str | None


@dataclass(frozen=True, slots=True)
class StoreSource:
    config: ServerConfig

    def audit(self, raw: dict) -> str:
        try:
            query = AuditQuery.parse(raw, self.config.policy)
        except QueryError as error:
            raise SourceError(str(error)) from error
        store = Store(self.config.db_path)
        try:
            sessions = store.sessions(query.user, query.since, query.session)
        finally:
            store.close()
        return report(sessions, self.config.policy, query)

    def roles(self) -> str:
        return render_roles(self.config.policy)


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
                    self._send(HTTPStatus.OK, StoreSource(self.config).roles(), CONTENT_TYPES["json"])
            case "/v1/audit":
                if self._authorized(self.config.read_token):
                    self._audit({k: v[-1] for k, v in parse_qs(url.query).items()})
            case "/mcp":
                self._reject(HTTPStatus.METHOD_NOT_ALLOWED, "mcp takes POST only\n")
            case _:
                self._send(HTTPStatus.NOT_FOUND, "not found\n")

    def do_POST(self) -> None:
        match urlsplit(self.path).path:
            case "/v1/ingest":
                if self._authorized(self.config.ingest_token) and (body := self._json_body()) is not None:
                    self._ingest(body)
            case "/mcp":
                if self._authorized(self.config.read_token) and (body := self._json_body()) is not None:
                    self._mcp(body)
            case _:
                self._reject(HTTPStatus.NOT_FOUND, "not found\n")

    def _ingest(self, body: object) -> None:
        try:
            sessions = decode_batch(body)
        except ValueError as error:
            self._send(HTTPStatus.BAD_REQUEST, f"{error}\n")
            return
        store = Store(self.config.db_path)
        try:
            stored = store.put(sessions)
        finally:
            store.close()
        self._send(HTTPStatus.OK, json.dumps({"stored": stored}), CONTENT_TYPES["json"])

    def _mcp(self, message: object) -> None:
        reply = respond(StoreSource(self.config), message)
        if reply is None:
            self._send(HTTPStatus.ACCEPTED, "")
        else:
            self._send(HTTPStatus.OK, json.dumps(reply), CONTENT_TYPES["json"])

    def _audit(self, raw: dict[str, str]) -> None:
        try:
            body = StoreSource(self.config).audit(raw)
        except SourceError as error:
            self._send(HTTPStatus.BAD_REQUEST, f"{error}\n")
            return
        self._send(HTTPStatus.OK, body, CONTENT_TYPES[raw.get("format") or "json"])

    def _json_body(self) -> object | None:
        length = self.headers.get("content-length", "")
        if not length.isdigit():
            self._reject(HTTPStatus.LENGTH_REQUIRED, "content-length required\n")
            return None
        if int(length) > MAX_BODY_BYTES:
            self._reject(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"body over {MAX_BODY_BYTES} bytes\n")
            return None
        try:
            return json.loads(self.rfile.read(int(length)))
        except ValueError as error:
            self._send(HTTPStatus.BAD_REQUEST, f"{error}\n")
            return None

    def _authorized(self, token: str | None) -> bool:
        if token is None:
            return True
        presented = self.headers.get("authorization", "").removeprefix("Bearer ")
        if hmac.compare_digest(presented.encode(), token.encode()):
            return True
        self._reject(HTTPStatus.UNAUTHORIZED, "unauthorized\n", {"www-authenticate": 'Bearer realm="clauditor"'})
        return False

    def _reject(self, status: HTTPStatus, body: str, headers: dict[str, str] | None = None) -> None:
        self.close_connection = True
        self._send(status, body, CONTENT_TYPES["text"], headers)

    def _send(self, status: HTTPStatus, body: str, content_type: str = CONTENT_TYPES["text"], headers: dict[str, str] | None = None) -> None:
        payload = body.encode()
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(payload)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        if self.close_connection:
            self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(payload)
