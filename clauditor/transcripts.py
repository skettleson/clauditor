from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

from .model import Activity, ActivityKind, EvidenceRef, Session

DEFAULT_PROJECTS_ROOT = Path.home() / ".claude" / "projects"

FILE_READ_TOOLS = frozenset({"Read", "Glob", "Grep", "NotebookRead"})
FILE_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
WEB_TOOLS = frozenset({"WebFetch", "WebSearch"})

SHELL_TOOL = "Bash"
MCP_PREFIX = "mcp__"
HUMAN_ORIGIN = "human"
SUBAGENT_FILE_PREFIX = "agent-"
HEREDOC_BODY = re.compile(r"(<<-?\s*(['\"]?)(\w+)\2[^\n]*\n).*?^\s*\3\s*$", re.DOTALL | re.MULTILINE)

KIND_BY_TOOL = (
    {SHELL_TOOL: ActivityKind.SHELL}
    | dict.fromkeys(FILE_READ_TOOLS, ActivityKind.FILE_READ)
    | dict.fromkeys(FILE_WRITE_TOOLS, ActivityKind.FILE_WRITE)
    | dict.fromkeys(WEB_TOOLS, ActivityKind.WEB)
)

SUBJECT_FIELD_BY_TOOL = {
    "Bash": "command",
    "Read": "file_path",
    "Edit": "file_path",
    "Write": "file_path",
    "MultiEdit": "file_path",
    "NotebookRead": "notebook_path",
    "NotebookEdit": "notebook_path",
    "Glob": "pattern",
    "Grep": "pattern",
    "WebFetch": "url",
    "WebSearch": "query",
}


def load_sessions(root: Path = DEFAULT_PROJECTS_ROOT, only: str | None = None) -> Iterator[Session]:
    for transcript in sorted(root.glob("*/*.jsonl")):
        session = load_session(transcript)
        if only is None or session.session_id.startswith(only):
            yield session


def load_session(transcript: Path) -> Session:
    events = list(_read_events(transcript))
    activities = [
        activity
        for line_no, event in events
        for activity in _activities_from_event(event, _evidence(transcript, line_no, event, None))
    ]
    for subagent_transcript in _subagent_transcripts(transcript):
        subagent_id = subagent_transcript.stem.removeprefix(SUBAGENT_FILE_PREFIX)
        activities.extend(
            activity
            for line_no, event in _read_events(subagent_transcript)
            for activity in _activities_from_event(event, _evidence(subagent_transcript, line_no, event, subagent_id))
        )
    activities.sort(key=lambda a: (a.evidence.timestamp, str(a.evidence.transcript), a.evidence.line_no))
    return Session(
        session_id=_first_field(events, "sessionId") or transcript.stem,
        project_cwd=_first_field(events, "cwd") or "",
        title=_last_title(events),
        started_at=_first_field(events, "timestamp") or "",
        activities=tuple(activities),
    )


def _read_events(transcript: Path) -> Iterator[tuple[int, dict]]:
    with transcript.open(encoding="utf-8", errors="replace") as lines:
        for line_no, line in enumerate(lines, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                yield line_no, event


def _first_field(events: list[tuple[int, dict]], field: str) -> str | None:
    return next((event[field] for _, event in events if isinstance(event.get(field), str)), None)


def _last_title(events: list[tuple[int, dict]]) -> str | None:
    titles = [event["aiTitle"] for _, event in events if event.get("type") == "ai-title" and event.get("aiTitle")]
    return titles[-1] if titles else None


def _evidence(transcript: Path, line_no: int, event: dict, subagent_id: str | None) -> EvidenceRef:
    return EvidenceRef(
        transcript=transcript,
        line_no=line_no,
        event_uuid=str(event.get("uuid", "")),
        timestamp=str(event.get("timestamp", "")),
        subagent_id=subagent_id,
    )


def _subagent_transcripts(transcript: Path) -> list[Path]:
    return sorted((transcript.parent / transcript.stem / "subagents").glob(f"{SUBAGENT_FILE_PREFIX}*.jsonl"))


def _activities_from_event(event: dict, evidence: EvidenceRef) -> list[Activity]:
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    match event.get("type"):
        case "user":
            prompt = _human_prompt_text(event, message.get("content"), evidence.subagent_id)
            return [Activity(ActivityKind.PROMPT, None, prompt, evidence)] if prompt else []
        case "assistant":
            content = message.get("content")
            blocks = content if isinstance(content, list) else []
            return [
                _activity_from_tool_use(block, evidence)
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "tool_use"
            ]
        case _:
            return []


def _human_prompt_text(event: dict, content: object, subagent_id: str | None) -> str | None:
    if event.get("isMeta") or subagent_id is not None:
        return None
    origin_kind = (event.get("origin") or {}).get("kind")
    if origin_kind == HUMAN_ORIGIN:
        return _text_of(content)
    if origin_kind is None and isinstance(content, str) and not content.lstrip().startswith("<"):
        return content
    return None


def _text_of(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
        return "\n".join(texts) or None
    return None


def _activity_from_tool_use(block: dict, evidence: EvidenceRef) -> Activity:
    tool_name = str(block.get("name", ""))
    tool_input = block.get("input")
    return Activity(
        kind=_kind_for_tool(tool_name),
        tool_name=tool_name,
        subject=_subject_for_tool(tool_name, tool_input if isinstance(tool_input, dict) else {}),
        evidence=evidence,
    )


def _kind_for_tool(tool_name: str) -> ActivityKind:
    if tool_name.startswith(MCP_PREFIX):
        return ActivityKind.MCP
    return KIND_BY_TOOL.get(tool_name, ActivityKind.TOOL)


def _subject_for_tool(tool_name: str, tool_input: dict) -> str:
    field = SUBJECT_FIELD_BY_TOOL.get(tool_name)
    if field is not None and isinstance(tool_input.get(field), str):
        value = tool_input[field]
        return HEREDOC_BODY.sub(r"\1", value) if tool_name == SHELL_TOOL else value
    serialized = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
    return f"{tool_name} {serialized}" if tool_name.startswith(MCP_PREFIX) else serialized
