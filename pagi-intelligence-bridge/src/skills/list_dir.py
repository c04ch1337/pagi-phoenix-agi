"""L5 Procedural Skill: list_dir – List directory contents safely (no recursion).

Discovery primitive for RLM: list → peek one → save derived result.
Allow-listed for local dispatch; Rust-mediated in production.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


class ListDirParams(BaseModel):
    path: str = "."
    pattern: Optional[str] = None  # Simple glob-like filter, e.g. "*.md" or ".md"
    max_items: int = 20  # Safety cap to prevent huge listings


def run(params: ListDirParams) -> str:
    try:
        dir_path = Path(params.path).resolve()
        if not dir_path.is_dir():
            return f"[list_dir] Not a directory: {params.path}"

        # Support "*.md" or ".md" as suffix filter
        suffix = None
        if params.pattern:
            p = params.pattern.strip().lower()
            suffix = p[1:] if p.startswith("*") else p

        items = []
        for entry in sorted(dir_path.iterdir(), key=lambda e: e.name.lower()):
            if suffix and not entry.name.lower().endswith(suffix):
                continue
            items.append(f"{entry.name} {'(dir)' if entry.is_dir() else '(file)'}")
            if len(items) >= params.max_items:
                items.append("... [truncated]")
                break

        if not items:
            return "[list_dir] Directory empty or no matches"

        return f"[list_dir] Contents of {dir_path}:\n" + "\n".join(items)
    except Exception as e:
        return f"[list_dir] Error: {type(e).__name__}: {e}"
