from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from .model import Activity, ActivityKind, EvidenceRef, Session

DEFAULT_PROJECTS_ROOT = Path.home() / ".claude" / "projects"

FILE_READ_TOOLS = frozenset({"Read", "Glob", "Grep", "NotebookRead"})
FILE_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
WEB_TOOLS = frozenset({"WebFetch", "WebSearch"})


def load_sessions(root: Path = DEFAULT_PROJECTS_ROOT, only: str | None = None) -> Iterator[Session]:
    raise NotImplementedError


def load_session(transcript: Path) -> Session:
    raise NotImplementedError


def _subagent_transcripts(transcript: Path) -> list[Path]:
    raise NotImplementedError


def _activities_from_event(event: dict, evidence: EvidenceRef) -> list[Activity]:
    raise NotImplementedError


def _activity_from_tool_use(block: dict, evidence: EvidenceRef) -> Activity:
    raise NotImplementedError


def _kind_for_tool(tool_name: str) -> ActivityKind:
    raise NotImplementedError


def _subject_for_tool(tool_name: str, tool_input: dict) -> str:
    raise NotImplementedError
