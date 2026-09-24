from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..model import Activity, ActivityKind, EvidenceRef, Session
from .common import ToolSpec, TranscriptFormat, argv, field, patched_files, read_events, tool_activity

TRAILING_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _web_subject(action: object) -> str | None:
    if not isinstance(action, dict):
        return None
    queries = action.get("queries") if isinstance(action.get("queries"), list) else []
    return next((value for value in (action.get("query"), action.get("url"), "\n".join(q for q in queries if isinstance(q, str))) if isinstance(value, str) and value), None)


TOOLS = {
    "exec_command": ToolSpec(ActivityKind.SHELL, field("cmd")),
    "shell_command": ToolSpec(ActivityKind.SHELL, field("command")),
    "shell": ToolSpec(ActivityKind.SHELL, argv("command")),
    "local_shell": ToolSpec(ActivityKind.SHELL, argv("command")),
    "apply_patch": ToolSpec(ActivityKind.FILE_WRITE, patched_files),
    "view_image": ToolSpec(ActivityKind.FILE_READ, field("path")),
    "read_file": ToolSpec(ActivityKind.FILE_READ, field("file_path")),
    "list_dir": ToolSpec(ActivityKind.FILE_READ, field("dir_path")),
    "grep_files": ToolSpec(ActivityKind.FILE_READ, field("pattern")),
    "web_search": ToolSpec(ActivityKind.WEB, _web_subject),
}


def transcripts(root: Path) -> list[Path]:
    return sorted(root.rglob("rollout-*.jsonl"))


def load_session(transcript: Path) -> Session:
    events = list(read_events(transcript))
    meta = next((event.get("payload") for _, event in events if event.get("type") == "session_meta"), None) or {}
    parent = meta.get("parent_thread_id") if isinstance(meta.get("parent_thread_id"), str) else None
    activities = [activity for line_no, event in events if (activity := _activity(transcript, line_no, event, parent is not None))]
    return Session(
        session_id=_session_id(transcript),
        project_cwd=meta.get("cwd") if isinstance(meta.get("cwd"), str) else "",
        title=f"subagent of {parent}" if parent else None,
        started_at=meta.get("timestamp") if isinstance(meta.get("timestamp"), str) else "",
        activities=tuple(activities),
        source=FORMAT.source,
    )


def _session_id(transcript: Path) -> str:
    match = TRAILING_UUID.search(transcript.stem)
    return match.group(0) if match else transcript.stem


def _activity(transcript: Path, line_no: int, event: dict, is_subagent: bool) -> Activity | None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    evidence = EvidenceRef(transcript, line_no, str(payload.get("call_id") or payload.get("id") or ""), str(event.get("timestamp", "")), None)
    match event.get("type"), payload.get("type"):
        case "event_msg", "user_message" if not is_subagent and _is_human_text(payload.get("message")):
            return Activity(ActivityKind.PROMPT, None, payload["message"], evidence)
        case "response_item", "function_call":
            return tool_activity(str(payload.get("name", "")), _arguments(payload.get("arguments")), TOOLS, evidence)
        case "response_item", "custom_tool_call":
            return tool_activity(str(payload.get("name", "")), payload.get("input", ""), TOOLS, evidence)
        case "response_item", "local_shell_call":
            return tool_activity("local_shell", payload.get("action") or {}, TOOLS, evidence)
        case "response_item", "web_search_call":
            return tool_activity("web_search", payload.get("action") or {}, TOOLS, evidence)
        case _:
            return None


def _is_human_text(message: object) -> bool:
    return isinstance(message, str) and bool(message.strip()) and not message.lstrip().startswith("<")


def _arguments(raw: object) -> object:
    if not isinstance(raw, str):
        return raw if isinstance(raw, dict) else {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


FORMAT = TranscriptFormat(
    source="codex",
    default_root=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions",
    transcripts=transcripts,
    companions=lambda transcript: [],
    load_session=load_session,
)
