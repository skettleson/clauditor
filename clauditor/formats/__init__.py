from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path

from ..model import Session
from . import claude_code, codex, cursor
from .common import TranscriptFormat

FORMATS: dict[str, TranscriptFormat] = {f.source: f for f in (claude_code.FORMAT, cursor.FORMAT, codex.FORMAT)}

Roots = Mapping[str, Path]


def default_roots() -> dict[str, Path]:
    return {source: f.default_root for source, f in FORMATS.items()}


def discover(roots: Roots) -> list[tuple[TranscriptFormat, Path]]:
    return [(FORMATS[source], transcript) for source, root in roots.items() for transcript in FORMATS[source].transcripts(root)]


def load_sessions(roots: Roots, only: str | None = None) -> Iterator[Session]:
    for transcript_format, transcript in discover(roots):
        session = transcript_format.load_session(transcript)
        if only is None or session.session_id.startswith(only):
            yield session
