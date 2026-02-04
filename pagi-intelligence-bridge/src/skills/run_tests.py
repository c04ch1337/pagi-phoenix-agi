"""L5 Procedural Skill: run_tests – Run tests in sandbox."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from pydantic import BaseModel


class RunTestsParams(BaseModel):
    dir: str
    type: str = "python"  # "python" or "rust"
    timeout_sec: int = 30


def _path_under_root(resolved: Path, root: Path) -> bool:
    try:
        resolved.relative_to(root.resolve())
        return True
    except ValueError:
        return False


def run(params: RunTestsParams) -> str:
    """Resolve dir, run pytest (python) or cargo test (rust) with timeout; return stdout/stderr summary or prefixed error."""
    try:
        root = Path(os.environ.get("PAGI_PROJECT_ROOT", ".")).resolve()
        dir_path = Path(params.dir).resolve()
        if not dir_path.exists():
            return f"[run_tests] Path not found: {params.dir}"
        if not dir_path.is_dir():
            return f"[run_tests] Not a directory: {params.dir}"
        if not _path_under_root(dir_path, root):
            return f"[run_tests] Path outside project root: {params.dir}"

        test_type = (params.type or "python").strip().lower()
        timeout = max(1, min(params.timeout_sec, 300))

        if test_type == "python":
            cmd = [
                os.environ.get("PAGI_POETRY", "poetry"),
                "run",
                "pytest",
                "-v",
                "--tb=short",
            ]
        elif test_type == "rust":
            cmd = ["cargo", "test"]
        else:
            return f"[run_tests] Unsupported type: {params.type} (use 'python' or 'rust')"

        result = subprocess.run(
            cmd,
            cwd=str(dir_path),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        summary_lines = [f"[run_tests] exit_code={result.returncode}"]
        if out:
            summary_lines.append("stdout:\n" + out[-8000:])
        if err:
            summary_lines.append("stderr:\n" + err[-4000:])
        return "\n".join(summary_lines)
    except subprocess.TimeoutExpired as e:
        return f"[run_tests] Execution timed out after {params.timeout_sec}s: {e}"
    except Exception as e:
        return f"[run_tests] Error: {type(e).__name__}: {e}"
