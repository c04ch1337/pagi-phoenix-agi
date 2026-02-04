"""L5 Procedural Skill: analyze_code – Analyze code snippet for errors/patterns in RCA.

NOTE: Stub for RCA primitives; no subprocess or external I/O. Execution surface remains
gated by PAGI_ALLOW_LOCAL_DISPATCH and allow-list.
"""

from __future__ import annotations

import re

from pydantic import BaseModel


class AnalyzeCodeParams(BaseModel):
    code: str
    language: str = "python"
    max_length: int = 4096


def run(params: AnalyzeCodeParams) -> str:
    """Analyze code snippet for errors/patterns; return RCA summary or [analyze_code] error."""
    code = params.code
    if len(code) > params.max_length:
        code = code[: params.max_length] + "\n# ... truncated"
    try:
        errors: list[str] = []
        # Stub: regex search for common error indicators
        if re.search(r"\bSyntaxError\b", code, re.IGNORECASE):
            errors.append("SyntaxError mentioned")
        if re.search(r"\bpanic\s*!\s*\(?", code):
            errors.append("panic! (Rust) detected")
        if re.search(r"\bpanic\b", code, re.IGNORECASE) and "panic!" not in code:
            if re.search(r"\bpanic\b", code):
                errors.append("panic reference")
        if re.search(r"undefined|NameError|AttributeError", code, re.IGNORECASE):
            errors.append("undefined/NameError/AttributeError pattern")
        if re.search(r"unwrap\s*\(\s*\)", code):
            errors.append("unwrap() may panic")
        summary = "RCA summary: " + (
            "; ".join(errors) if errors else "No obvious error patterns found (stub analysis)."
        )
        return summary
    except Exception as e:
        return f"[analyze_code] Error: {type(e).__name__}: {e}"
