from __future__ import annotations

from typing import Literal

from .model import SessionAudit

ReportFormat = Literal["text", "markdown", "json"]


def render(audits: list[SessionAudit], fmt: ReportFormat = "text", show_aligned: bool = False) -> str:
    raise NotImplementedError
