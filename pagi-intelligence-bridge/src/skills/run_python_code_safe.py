"""L5 Procedural Skill: run_python_code_safe – Execute Python code in sandbox."""

from __future__ import annotations

import io
import sys
import threading
from typing import Any

from pydantic import BaseModel


class RunPythonCodeSafeParams(BaseModel):
    code: str
    timeout_sec: int = 5
    max_output_len: int = 4096


# Whitelist of builtins allowed in sandbox (no os, subprocess, open, eval, exec, __import__, input, etc.)
_SAFE_BUILTINS: set[str] = {
    "abs", "all", "any", "bool", "callable", "chr", "dict", "divmod", "enumerate",
    "filter", "float", "frozenset", "int", "isinstance", "issubclass", "iter", "len",
    "list", "map", "max", "min", "next", "object", "ord", "pow", "print", "range",
    "repr", "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple",
    "type", "zip",
    "ArithmeticError", "AssertionError", "AttributeError", "BaseException", "EOFError",
    "Exception", "False", "FloatingPointError", "GeneratorExit", "IndexError",
    "KeyError", "LookupError", "MemoryError", "None", "NotImplementedError",
    "OverflowError", "RuntimeError", "StopIteration", "True", "TypeError",
    "UnboundLocalError", "UnicodeError", "ValueError", "ZeroDivisionError",
}


def _restricted_builtins() -> dict[str, Any]:
    b = __builtins__ if isinstance(__builtins__, dict) else __builtins__.__dict__
    return {k: b[k] for k in _SAFE_BUILTINS if k in b}


def run(params: RunPythonCodeSafeParams) -> str:
    """Execute code in restricted globals with timeout; capture stdout; return output or prefixed error."""
    code = params.code
    timeout_sec = max(1, min(params.timeout_sec, 30))
    max_out = max(0, min(params.max_output_len, 65536))

    restricted_globals: dict[str, Any] = {
        "__builtins__": _restricted_builtins(),
        "__name__": "__main__",
    }
    result_container: list[str | tuple[str, BaseException]] = []

    def run_code() -> None:
        try:
            old = sys.stdout
            sys.stdout = io.StringIO()
            try:
                exec(code, restricted_globals)
                out = sys.stdout.getvalue()
                result_container.append(out)
            finally:
                sys.stdout = old
        except BaseException as e:
            result_container.append(("error", e))

    thread = threading.Thread(target=run_code, daemon=True)
    thread.start()
    thread.join(timeout=timeout_sec)

    if thread.is_alive():
        return f"[run_python_code_safe] Execution timed out after {timeout_sec}s"

    if not result_container:
        return "[run_python_code_safe] No output captured"

    raw = result_container[0]
    if isinstance(raw, tuple) and raw[0] == "error":
        exc = raw[1]
        return f"[run_python_code_safe] Error: {type(exc).__name__}: {exc}"

    out = raw[:max_out]
    if len(raw) > max_out:
        out += f"\n... [truncated, max_output_len={params.max_output_len}]"
    return out.strip() or "[run_python_code_safe] (no stdout)"
