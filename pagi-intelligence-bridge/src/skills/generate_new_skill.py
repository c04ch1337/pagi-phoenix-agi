"""L5 Procedural Skill: generate_new_skill – Propose a new skill module from a self-patch.

This is called by the Rust Watchdog auto-evolve hook (after a successful python_skill self-patch apply)
when `PAGI_AUTO_EVOLVE_SKILLS=true`.

Contract:
- Input: patch_content (string)
- Output: JSON string: {"filename": "<name>.py", "code": "..."}

The Rust side will persist the file via `write_file_safe` and then commit in the bridge repo.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from pydantic import BaseModel


class GenerateNewSkillParams(BaseModel):
    patch_content: str
    max_patch_chars: int = 12000


_SAFE_STEM_RE = re.compile(r"[^a-zA-Z0-9_]+")


def _derive_name_hint(patch: str) -> str:
    """Heuristic: try to pick a stable-ish name from the patch content."""
    # Look for something that resembles a function/skill name in the patch.
    m = re.search(r"\bdef\s+([a-zA-Z_][a-zA-Z0-9_]*)\b", patch)
    if m:
        return m.group(1)
    m = re.search(r"\bpub\s+fn\s+([a-zA-Z_][a-zA-Z0-9_]*)\b", patch)
    if m:
        return m.group(1)
    # Fallback: timestamped.
    return f"evolved_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _safe_filename(stem: str) -> str:
    stem = stem.strip()
    stem = _SAFE_STEM_RE.sub("_", stem)
    stem = stem.strip("_")
    if not stem:
        stem = f"evolved_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return f"{stem}.py"


def run(params: GenerateNewSkillParams) -> str:
    patch = params.patch_content or ""
    if len(patch) > params.max_patch_chars:
        patch = patch[: params.max_patch_chars] + "\n# ... truncated"

    name_hint = _derive_name_hint(patch)
    filename = _safe_filename(name_hint)

    snippet = patch[:800].replace("\r\n", "\n").splitlines()
    snippet = "\n".join(f"# {line}" for line in snippet)

    code = f'''"""Auto-evolved skill from self-patch.

Filename: {filename}
Generated: {datetime.now().isoformat(timespec="seconds")}

This skill is intentionally conservative: it contains no subprocess execution and no outbound I/O.
It should be reviewed before being used for any side-effectful operations.
"""

from __future__ import annotations

from pydantic import BaseModel


class {"".join(w.capitalize() for w in filename.removesuffix(".py").split("_"))}Params(BaseModel):
    """Parameters for the evolved skill (initially empty; extend as needed)."""

    pass


def run(params: {"".join(w.capitalize() for w in filename.removesuffix(".py").split("_"))}Params) -> str:
    """Return a deterministic observation; patch snippet is embedded below for traceability."""

    return "auto_evolved_skill_ok"


# Patch snippet (traceability):
# ---
{snippet}
# ---
'''

    return json.dumps({"filename": filename, "code": code})

