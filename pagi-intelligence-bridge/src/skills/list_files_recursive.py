"""L5 Procedural Skill: list_files_recursive – Recursive directory listing with safety caps.

Discovery primitive for RLM: recursive walk with depth cap, pattern filter, max_items.
Allow-listed for local dispatch; Rust-mediated in production.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


class ListFilesRecursiveParams(BaseModel):
    path: str = "."
    pattern: Optional[str] = None  # Suffix filter, e.g. "*.py" or ".py"
    max_depth: int = 3
    max_items: int = 100


def run(params: ListFilesRecursiveParams) -> str:
    """Recursive directory listing with depth cap, optional pattern filter, and max_items truncation."""
    try:
        base = Path(params.path).resolve()
        if not base.is_dir():
            return f"[list_files_recursive] Not a directory: {params.path}"

        suffix = None
        if params.pattern:
            p = params.pattern.strip().lower()
            suffix = p[1:] if p.startswith("*") else (p if p.startswith(".") else f".{p}")

        collected: list[str] = []
        base_resolved = base.resolve()
        for root, dirs, files in os.walk(base_resolved, topdown=True):
            root_path = Path(root).resolve()
            try:
                rel_parts = root_path.relative_to(base_resolved).parts
            except ValueError:
                rel_parts = ()
            depth = len(rel_parts)
            if depth >= params.max_depth:
                dirs.clear()
                continue
            rel_root = Path(*rel_parts) if rel_parts else Path(".")
            for name in sorted(files):
                if suffix and not name.lower().endswith(suffix):
                    continue
                rel_path = rel_root / name if rel_root != Path(".") else name
                collected.append(str(rel_path).replace("\\", "/"))
                if len(collected) >= params.max_items:
                    collected.append("... [truncated]")
                    return "\n".join(collected)
            if len(collected) >= params.max_items:
                collected.append("... [truncated]")
                return "\n".join(collected)

        if not collected:
            return "[list_files_recursive] No files matched or directory empty"
        return "\n".join(collected)
    except Exception as e:
        return f"[list_files_recursive] Error: {type(e).__name__}: {e}"
