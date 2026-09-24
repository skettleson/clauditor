from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..model import Activity, ActivityKind, EvidenceRef, Session
from .common import MCP_PREFIX, ToolSpec, TranscriptFormat, created_at, field, iso_utc, patched_files, read_events, tool_activity

USER_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)
TURN_TIMESTAMP = re.compile(r"<timestamp>\w+, (\w+ \d{1,2}, \d{4}, \d{1,2}:\d{2} [AP]M) \(UTC(?:([+-])(\d{1,2})(?::(\d{2}))?)?\)</timestamp>")
SLUG_SEPARATOR = re.compile(r"[^A-Za-z0-9]")

TOOLS = {
    "Shell": ToolSpec(ActivityKind.SHELL, field("command")),
    "Read": ToolSpec(ActivityKind.FILE_READ, field("path")),
    "ReadFile": ToolSpec(ActivityKind.FILE_READ, field("path")),
    "Grep": ToolSpec(ActivityKind.FILE_READ, field("pattern")),
    "Glob": ToolSpec(ActivityKind.FILE_READ, field("glob_pattern")),
    "StrReplace": ToolSpec(ActivityKind.FILE_WRITE, field("path")),
    "Write": ToolSpec(ActivityKind.FILE_WRITE, field("path")),
    "Delete": ToolSpec(ActivityKind.FILE_WRITE, field("path")),
    "EditNotebook": ToolSpec(ActivityKind.FILE_WRITE, field("target_notebook")),
    "ApplyPatch": ToolSpec(ActivityKind.FILE_WRITE, patched_files),
    "WebSearch": ToolSpec(ActivityKind.WEB, field("search_term")),
    "WebFetch": ToolSpec(ActivityKind.WEB, field("url")),
}


def transcripts(root: Path) -> list[Path]:
    return sorted(path for path in root.glob("*/agent-transcripts/*/*.jsonl") if path.stem == path.parent.name)


def subagent_transcripts(transcript: Path) -> list[Path]:
    return sorted((transcript.parent / "subagents").glob("*.jsonl"))


def load_session(transcript: Path) -> Session:
    activities = _activities(transcript, None, created_at(transcript))
    for subagent_transcript in subagent_transcripts(transcript):
        activities.extend(_activities(subagent_transcript, subagent_transcript.stem, created_at(subagent_transcript)))
    activities.sort(key=lambda a: (a.evidence.timestamp, str(a.evidence.transcript), a.evidence.line_no))
    prompts = [a for a in activities if a.kind is ActivityKind.PROMPT]
    return Session(
        session_id=transcript.stem,
        project_cwd=workspace_path(transcript.parents[2].name),
        title=None,
        started_at=prompts[0].evidence.timestamp if prompts else created_at(transcript),
        activities=tuple(activities),
        source=FORMAT.source,
    )


def workspace_path(slug: str, filesystem_root: Path = Path("/")) -> str:
    if slug == "empty-window":
        return ""
    words = slug.split("-")
    resolved = filesystem_root
    while words:
        match = _longest_child(resolved, words)
        if match is None:
            return str(resolved.joinpath(*words))
        resolved, words = match
    return str(resolved)


def _longest_child(directory: Path, words: list[str]) -> tuple[Path, list[str]] | None:
    try:
        children = {SLUG_SEPARATOR.sub("-", child.name): child for child in directory.iterdir() if child.is_dir()}
    except OSError:
        return None
    for end in range(len(words), 0, -1):
        child = children.get("-".join(words[:end]))
        if child is not None:
            return child, words[end:]
    return None


def _activities(transcript: Path, subagent_id: str | None, fallback_timestamp: str) -> list[Activity]:
    activities = []
    timestamp = fallback_timestamp
    for line_no, event in read_events(transcript):
        message = event.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        if not isinstance(blocks, list):
            continue
        blocks = [block for block in blocks if isinstance(block, dict)]
        if event.get("role") == "user" and subagent_id is None:
            text = "\n".join(block.get("text", "") for block in blocks if block.get("type") == "text")
            timestamp = _turn_timestamp(text) or timestamp
            evidence = EvidenceRef(transcript, line_no, "", timestamp, subagent_id)
            activities += [Activity(ActivityKind.PROMPT, None, query, evidence) for query in USER_QUERY.findall(text)]
        elif event.get("role") == "assistant":
            evidence = EvidenceRef(transcript, line_no, "", timestamp, subagent_id)
            activities += [_tool_activity(block, evidence) for block in blocks if block.get("type") == "tool_use"]
    return activities


def _tool_activity(block: dict, evidence: EvidenceRef) -> Activity:
    name, tool_input = str(block.get("name", "")), block.get("input")
    if name == "CallMcpTool" and isinstance(tool_input, dict):
        name = f"{MCP_PREFIX}{tool_input.get('server', '')}__{tool_input.get('toolName', '')}"
        tool_input = tool_input.get("arguments", {})
    return tool_activity(name, tool_input if isinstance(tool_input, (dict, str)) else {}, TOOLS, evidence)


def _turn_timestamp(text: str) -> str | None:
    match = TURN_TIMESTAMP.search(text)
    if match is None:
        return None
    local, sign, hours, minutes = match.groups()
    offset = timedelta(hours=int(hours or 0), minutes=int(minutes or 0)) * (-1 if sign == "-" else 1)
    try:
        moment = datetime.strptime(local, "%b %d, %Y, %I:%M %p").replace(tzinfo=timezone(offset))
    except ValueError:
        return None
    return iso_utc(moment)


FORMAT = TranscriptFormat(
    source="cursor",
    default_root=Path.home() / ".cursor" / "projects",
    transcripts=transcripts,
    companions=subagent_transcripts,
    load_session=load_session,
)
