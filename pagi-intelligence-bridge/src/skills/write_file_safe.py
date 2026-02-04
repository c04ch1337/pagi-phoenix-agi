"""L5 Procedural Skill: write_file_safe – Write content to file with safety constraints."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel


class WriteFileSafeParams(BaseModel):
    path: str
    content: str
    max_content_bytes: int = 1048576  # 1 MiB
    overwrite: bool = False


def _path_under_root(resolved: Path, root: Path) -> bool:
    try:
        resolved.relative_to(root.resolve())
        return True
    except ValueError:
        return False


def run(params: WriteFileSafeParams) -> str:
    """Sanitize path, enforce overwrite flag, cap content size, write utf-8."""
    try:
        resolved = Path(params.path).resolve()
        root = Path(os.environ.get("PAGI_PROJECT_ROOT", ".")).resolve()
        if not _path_under_root(resolved, root):
            return f"[write_file_safe] Path outside project root: {params.path}"

        if resolved.exists() and not params.overwrite:
            return f"[write_file_safe] File exists and overwrite=false: {params.path}"

        content = params.content
        if len(content.encode("utf-8")) > params.max_content_bytes:
            # Truncate to fit max_content_bytes in UTF-8
            encoded = content.encode("utf-8")
            content = encoded[: params.max_content_bytes].decode("utf-8", errors="replace")

        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return f"[write_file_safe] Wrote {len(content.encode('utf-8'))} bytes to {resolved}"
    except Exception as e:
        return f"[write_file_safe] Error: {type(e).__name__}: {e}"
