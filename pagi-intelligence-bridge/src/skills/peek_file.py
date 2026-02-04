"""L5 Procedural Skill: peek_file

Safe file snippet reader for RLM context.

NOTE: This is a *library* skill stub. It does not broaden the execution surface by itself.
Real execution should remain Rust-mediated (ExecuteAction allow-list + sandbox) when enabled.
"""

from __future__ import annotations

import os

from pydantic import BaseModel


class PeekFileParams(BaseModel):
    path: str
    start: int = 0
    end: int = 2000
    encoding: str = "utf-8"


def run(params: PeekFileParams) -> str:
    if not os.path.isfile(params.path):
        return f"[peek_file] File not found: {params.path}"

    if params.start < 0:
        return "[peek_file] Invalid start"
    if params.end < params.start:
        return "[peek_file] Invalid range"

    try:
        with open(params.path, "r", encoding=params.encoding, errors="replace") as f:
            f.seek(params.start)
            content = f.read(params.end - params.start)
        return content
    except Exception as e:
        return f"[peek_file] Error: {type(e).__name__}: {e}"

