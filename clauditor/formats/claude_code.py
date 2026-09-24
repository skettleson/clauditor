from __future__ import annotations

from pathlib import Path

from ..model import Activity, ActivityKind, EvidenceRef, Session
from .common import ToolSpec, TranscriptFormat, field, read_events, tool_activity

HUMAN_ORIGIN = "human"
SUBAGENT_FILE_PREFIX = "agent-"

TOOLS = {
    "Bash": ToolSpec(ActivityKind.SHELL, field("command")),
    "Read": ToolSpec(ActivityKind.FILE_READ, field("file_path")),
    "Glob": ToolSpec(ActivityKind.FILE_READ, field("pattern")),
    "Grep": ToolSpec(ActivityKind.FILE_READ, field("pattern")),
    "NotebookRead": ToolSpec(ActivityKind.FILE_READ, field("notebook_path")),
    "Edit": ToolSpec(ActivityKind.FILE_WRITE, field("file_path")),
    "Write": ToolSpec(ActivityKind.FILE_WRITE, field("file_path")),
    "MultiEdit": ToolSpec(ActivityKind.FILE_WRITE, field("file_path")),
    "NotebookEdit": ToolSpec(ActivityKind.FILE_WRITE, field("notebook_path")),
    "WebFetch": ToolSpec(ActivityKind.WEB, field("url")),
    "WebSearch": ToolSpec(ActivityKind.WEB, field("query")),
}


def transcripts(root: Path) -> list[Path]:
    return sorted(root.glob("*/*.jsonl"))


def subagent_transcripts(transcript: Path) -> list[Path]:
    return sorted((transcript.parent / transcript.stem / "subagents").glob(f"{SUBAGENT_FILE_PREFIX}*.jsonl"))


def load_session(transcript: Path) -> Session:
    events = list(read_events(transcript))
    activities = [
        activity
        for line_no, event in events
        for activity in _activities_from_event(event, _evidence(transcript, line_no, event, None))
    ]
    for subagent_transcript in subagent_transcripts(transcript):
        subagent_id = subagent_transcript.stem.removeprefix(SUBAGENT_FILE_PREFIX)
        activities.extend(
            activity
            for line_no, event in read_events(subagent_transcript)
            for activity in _activities_from_event(event, _evidence(subagent_transcript, line_no, event, subagent_id))
        )
    activities.sort(key=lambda a: (a.evidence.timestamp, str(a.evidence.transcript), a.evidence.line_no))
    return Session(
        session_id=transcript.stem,
        project_cwd=_first_field(events, "cwd") or "",
        title=_last_title(events),
        started_at=_first_field(events, "timestamp") or "",
        activities=tuple(activities),
        source=FORMAT.source,
    )


def _first_field(events: list[tuple[int, dict]], name: str) -> str | None:
    return next((event[name] for _, event in events if isinstance(event.get(name), str)), None)


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
                tool_activity(str(block.get("name", "")), block.get("input") if isinstance(block.get("input"), dict) else {}, TOOLS, evidence)
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


FORMAT = TranscriptFormat(
    source="claude-code",
    default_root=Path.home() / ".claude" / "projects",
    transcripts=transcripts,
    companions=subagent_transcripts,
    load_session=load_session,
)
