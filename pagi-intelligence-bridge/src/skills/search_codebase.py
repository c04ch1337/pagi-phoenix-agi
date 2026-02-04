"""L5 Procedural Skill: search_codebase – Search codebase for patterns."""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import BaseModel


class SearchCodebaseParams(BaseModel):
    path: str = "."
    pattern: str
    mode: str = "keyword"  # "keyword" or "regex"
    max_files: int = 50


def _path_under_root(resolved: Path, root: Path) -> bool:
    try:
        resolved.relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _is_text_file(path: Path) -> bool:
    """Heuristic: skip binary by extension and try decode."""
    skip_suffixes = {".pyc", ".so", ".dll", ".exe", ".bin", ".png", ".jpg", ".ico", ".woff", ".ttf"}
    if path.suffix.lower() in skip_suffixes:
        return False
    return True


def run(params: SearchCodebaseParams) -> str:
    """Resolve path, walk dir (cap at max_files), search for pattern; return file:line matches or prefixed error."""
    try:
        root = Path(os.environ.get("PAGI_PROJECT_ROOT", ".")).resolve()
        dir_path = Path(params.path).resolve()
        if not dir_path.exists():
            return f"[search_codebase] Path not found: {params.path}"
        if not dir_path.is_dir():
            return f"[search_codebase] Not a directory: {params.path}"
        if not _path_under_root(dir_path, root):
            return f"[search_codebase] Path outside project root: {params.path}"

        if params.mode == "regex":
            try:
                pattern_re = re.compile(params.pattern)
            except re.error as e:
                return f"[search_codebase] Invalid regex: {e}"
        else:
            pattern_str = re.escape(params.pattern)

        matches: list[str] = []
        files_processed = 0

        for entry in sorted(dir_path.rglob("*")):
            if files_processed >= params.max_files:
                matches.append(f"... [truncated at {params.max_files} files]")
                break
            if not entry.is_file() or not _is_text_file(entry):
                continue
            try:
                if not _path_under_root(entry, root):
                    continue
            except Exception:
                continue
            files_processed += 1
            try:
                content = entry.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(content.splitlines(), start=1):
                if params.mode == "regex":
                    if pattern_re.search(line):
                        matches.append(f"{entry}:{i}: {line.strip()[:200]}")
                else:
                    if pattern_str in line:
                        matches.append(f"{entry}:{i}: {line.strip()[:200]}")

        if not matches:
            return f"[search_codebase] No matches for pattern in {params.path} (files scanned: {files_processed})"
        return "[search_codebase] Matches:\n" + "\n".join(matches[:100])
    except Exception as e:
        return f"[search_codebase] Error: {type(e).__name__}: {e}"
