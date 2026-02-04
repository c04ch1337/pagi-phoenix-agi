"""L5 Procedural Skill: read_entire_file_safe – Read full file content with safety caps."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class ReadEntireFileSafeParams(BaseModel):
    path: str
    max_size_bytes: int = 1048576  # 1 MiB
    encoding: str = "utf-8"


def run(params: ReadEntireFileSafeParams) -> str:
    """Resolve path, check file exists, read up to max_size_bytes (truncate if larger)."""
    try:
        resolved = Path(params.path).resolve()
        if not resolved.exists() or not resolved.is_file():
            return f"[read_entire_file_safe] Not a file or not found: {params.path}"

        with open(
            resolved, "r", encoding=params.encoding, errors="replace"
        ) as f:
            content = f.read(params.max_size_bytes)
        return content
    except Exception as e:
        return f"[read_entire_file_safe] Error: {type(e).__name__}: {e}"
