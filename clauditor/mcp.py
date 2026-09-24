from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import IO, Protocol

from .formats import Roots, load_sessions
from .model import Policy
from .query import AuditQuery, QueryError, report
from .report import render_roles

SERVER_NAME = "clauditor"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
METHOD_NOT_FOUND = -32601
PARSE_ERROR = -32700
INVALID_REQUEST = -32600

TOOLS = [
    {
        "name": "audit_sessions",
        "description": "Audit Claude Code, Cursor and Codex agent sessions against each operator's role. Returns drifted and review sessions first, and cites the transcript file, line and event uuid behind every forbidden finding.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "Operator email or local username. Central server only."},
                "since": {"type": "string", "description": "ISO 8601 timestamp. Only sessions started at or after it."},
                "session": {"type": "string", "description": "Session id or id prefix."},
                "role": {"type": "string", "description": "Audit every session as this role instead of the assigned one."},
                "format": {"type": "string", "enum": ["markdown", "json", "text"], "default": "markdown"},
                "show_aligned": {"type": "boolean", "default": False, "description": "Include aligned sessions."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "list_roles",
        "description": "List the roles, their expected and forbidden capabilities, and which users map to which role.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


class SourceError(RuntimeError):
    pass


class Source(Protocol):
    def audit(self, raw: dict) -> str: ...
    def roles(self) -> str: ...


@dataclass(frozen=True, slots=True)
class RemoteSource:
    server: str
    token: str | None

    def audit(self, raw: dict) -> str:
        return self._get("/v1/audit", {k: str(v).lower() if isinstance(v, bool) else v for k, v in raw.items()})

    def roles(self) -> str:
        return self._get("/v1/roles", {})

    def _get(self, path: str, params: dict) -> str:
        url = f"{self.server.rstrip('/')}{path}?{urllib.parse.urlencode(params)}"
        headers = {"authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
                return response.read().decode()
        except urllib.error.HTTPError as error:
            raise SourceError(f"{error.code} {error.read().decode().strip()}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise SourceError(f"cannot reach {self.server}: {error}") from error


@dataclass(frozen=True, slots=True)
class LocalSource:
    roots: Roots
    policy: Policy

    def audit(self, raw: dict) -> str:
        try:
            query = AuditQuery.parse(raw, self.policy)
        except QueryError as error:
            raise SourceError(str(error)) from error
        if query.user:
            raise SourceError("user filter needs the central server, local transcripts have one operator")
        sessions = (s for s in load_sessions(self.roots, query.session) if not query.since or s.started_at >= query.since)
        return report(sessions, self.policy, query)

    def roles(self) -> str:
        return render_roles(self.policy)


def serve_stdio(source: Source, stdin: IO[str] = sys.stdin, stdout: IO[str] = sys.stdout) -> None:
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as error:
            _write(stdout, {"jsonrpc": "2.0", "id": None, "error": {"code": PARSE_ERROR, "message": str(error)}})
            continue
        if (reply := respond(source, message)) is not None:
            _write(stdout, reply)


def respond(source: Source, message: object) -> dict | None:
    if not isinstance(message, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": INVALID_REQUEST, "message": "expected one JSON-RPC object"}}
    if "id" not in message:
        return None
    return {"jsonrpc": "2.0", "id": message["id"]} | handle(source, message.get("method"), message.get("params") or {})


def handle(source: Source, method: object, params: dict) -> dict:
    match method:
        case "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
            return {"result": {"protocolVersion": version, "capabilities": {"tools": {}}, "serverInfo": {"name": SERVER_NAME, "version": "0.1.0"}}}
        case "ping":
            return {"result": {}}
        case "tools/list":
            return {"result": {"tools": TOOLS}}
        case "tools/call":
            return {"result": _call_tool(source, params.get("name"), params.get("arguments") or {})}
        case _:
            return {"error": {"code": METHOD_NOT_FOUND, "message": f"unknown method {method!r}"}}


def _call_tool(source: Source, name: object, arguments: dict) -> dict:
    try:
        match name:
            case "audit_sessions":
                text = source.audit({"format": "markdown"} | arguments)
            case "list_roles":
                text = source.roles()
            case _:
                raise SourceError(f"unknown tool {name!r}")
    except SourceError as error:
        return {"content": [{"type": "text", "text": str(error)}], "isError": True}
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _write(stdout: IO[str], message: dict) -> None:
    stdout.write(json.dumps(message) + "\n")
    stdout.flush()
