from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..model import Activity, ActivityKind, EvidenceRef, Session

MCP_PREFIX = "mcp__"
HEREDOC_BODY = re.compile(r"(<<-?\s*(['\"]?)(\w+)\2[^\n]*\n).*?^\s*\3\s*$", re.DOTALL | re.MULTILINE)
PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
SHELL_SCRIPT_FLAGS = frozenset({"-c", "-lc"})

Subject = Callable[[object], str | None]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    kind: ActivityKind
    subject: Subject


@dataclass(frozen=True, slots=True)
class TranscriptFormat:
    source: str
    default_root: Path
    transcripts: Callable[[Path], list[Path]]
    companions: Callable[[Path], list[Path]]
    load_session: Callable[[Path], Session]


def field(name: str) -> Subject:
    return lambda raw: raw[name] if isinstance(raw, dict) and isinstance(raw.get(name), str) else None


def argv(name: str) -> Subject:
    def subject(raw: object) -> str | None:
        words = raw.get(name) if isinstance(raw, dict) else None
        if not isinstance(words, list) or not all(isinstance(w, str) for w in words):
            return None
        return words[2] if len(words) >= 3 and words[1] in SHELL_SCRIPT_FLAGS else shlex.join(words)

    return subject


def patched_files(raw: object) -> str | None:
    text = raw.get("input") if isinstance(raw, dict) else raw
    return "\n".join(PATCH_FILE.findall(text)) or None if isinstance(text, str) else None


def tool_activity(tool_name: str, tool_input: object, specs: Mapping[str, ToolSpec], evidence: EvidenceRef) -> Activity:
    if tool_name.startswith(MCP_PREFIX):
        return Activity(ActivityKind.MCP, tool_name, f"{tool_name} {_serialized(tool_input)}", evidence)
    spec = specs.get(tool_name)
    kind = spec.kind if spec else ActivityKind.TOOL
    subject = spec.subject(tool_input) if spec else None
    if subject is None:
        subject = _serialized(tool_input)
    elif kind is ActivityKind.SHELL:
        subject = HEREDOC_BODY.sub(r"\1", subject)
    return Activity(kind, tool_name, subject, evidence)


def read_events(transcript: Path) -> Iterator[tuple[int, dict]]:
    with transcript.open(encoding="utf-8", errors="replace") as lines:
        for line_no, line in enumerate(lines, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                yield line_no, event


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def created_at(path: Path) -> str:
    stat = path.stat()
    return iso_utc(datetime.fromtimestamp(getattr(stat, "st_birthtime", stat.st_mtime), UTC))


def _serialized(tool_input: object) -> str:
    return tool_input if isinstance(tool_input, str) else json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
