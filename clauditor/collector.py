from __future__ import annotations

import getpass
import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path

from .model import Owner, Session
from .transcripts import DEFAULT_PROJECTS_ROOT, load_session, session_transcripts
from .wire import encode_batch

DEFAULT_STATE_FILE = Path.home() / ".clauditor" / "collector-state.json"
CLAUDE_ACCOUNT_FILE = Path.home() / ".claude.json"
BATCH_SIZE = 20


@dataclass(frozen=True, slots=True)
class CollectResult:
    shipped: int
    unchanged: int
    failed: int
    last_error: str | None = None


class ShipError(RuntimeError):
    pass


def local_owner(account_file: Path = CLAUDE_ACCOUNT_FILE) -> Owner:
    try:
        account = json.loads(account_file.read_text(encoding="utf-8")).get("oauthAccount") or {}
    except (OSError, ValueError):
        account = {}
    email = account.get("emailAddress") if isinstance(account.get("emailAddress"), str) else None
    return Owner(host=socket.gethostname(), local_user=getpass.getuser(), email=email)


def collect(server: str, token: str | None, root: Path = DEFAULT_PROJECTS_ROOT, state_file: Path = DEFAULT_STATE_FILE, owner: Owner | None = None) -> CollectResult:
    owner = owner or local_owner()
    shipped_fingerprints = _read_state(state_file)
    pending = [(t, fp) for t in session_transcripts(root) if shipped_fingerprints.get(str(t)) != (fp := _fingerprint(t))]
    unchanged = sum(1 for _ in session_transcripts(root)) - len(pending)
    shipped = failed = 0
    last_error = None
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        sessions = [replace(load_session(transcript), owner=owner) for transcript, _ in batch]
        try:
            ship(server, token, owner, sessions)
        except ShipError as error:
            failed += len(batch)
            last_error = str(error)
            continue
        shipped += len(batch)
        shipped_fingerprints.update({str(transcript): fp for transcript, fp in batch})
        _write_state(state_file, shipped_fingerprints)
    return CollectResult(shipped, unchanged, failed, last_error)


def ship(server: str, token: str | None, owner: Owner, sessions: list[Session]) -> None:
    request = urllib.request.Request(
        f"{server.rstrip('/')}/v1/ingest",
        data=json.dumps(encode_batch(owner, sessions)).encode(),
        headers={"content-type": "application/json"} | ({"authorization": f"Bearer {token}"} if token else {}),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response.read()
    except urllib.error.HTTPError as error:
        with error:
            raise ShipError(f"{error.code} {error.read().decode(errors='replace').strip()}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise ShipError(str(error)) from error


def _fingerprint(transcript: Path) -> str:
    files = [transcript, *sorted((transcript.parent / transcript.stem / "subagents").glob("*.jsonl"))]
    return ";".join(f"{f.name}:{f.stat().st_size}:{f.stat().st_mtime_ns}" for f in files)


def _read_state(state_file: Path) -> dict[str, str]:
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write_state(state_file: Path, state: dict[str, str]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_file.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=0, sort_keys=True), encoding="utf-8")
    temporary.replace(state_file)
