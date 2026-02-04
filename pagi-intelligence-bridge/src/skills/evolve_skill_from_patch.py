"""L5 Procedural Skill: evolve_skill_from_patch – Propose new skill from patch content (auto-evolve).

Called by Watchdog after successful python_skill apply when PAGI_AUTO_EVOLVE_SKILLS=true.
Writes a stub skill to src/skills/evolved_<timestamp>.py and returns EVOLVED_PATH for Git commit.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel


class EvolveSkillFromPatchParams(BaseModel):
    patch_content: str
    max_patch_chars: int = 8192


def _skills_dir() -> Path:
    return Path(__file__).resolve().parent


def run(params: EvolveSkillFromPatchParams) -> str:
    """Generate stub skill from patch content; write to skills/evolved_<timestamp>.py; return EVOLVED_PATH for Rust."""
    content = params.patch_content
    if len(content) > params.max_patch_chars:
        content = content[: params.max_patch_chars] + "\n# ... truncated"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"evolved_{ts}.py"
    skills_dir = _skills_dir()
    skills_dir.mkdir(parents=True, exist_ok=True)
    path = skills_dir / name
    snippet = content[:500].replace("\n", "\n# ")
    stub = f'''"""Auto-evolved skill from self-patch (L5)."""

from __future__ import annotations

from pydantic import BaseModel


class EvolvedParams(BaseModel):
    pass


def run(params: EvolvedParams) -> str:
    """Stub evolved from patch."""
    return "evolved_stub_ok"


# Patch snippet for traceability:
# ---
# {snippet}
# ---
'''
    path.write_text(stub, encoding="utf-8")
    # Relative to bridge root (parent of src)
    bridge_root = path.resolve().parent.parent.parent  # src/skills -> src -> bridge root
    rel = path.resolve().relative_to(bridge_root)
    rel_str = str(rel).replace("\\", "/")
    return f"EVOLVED_PATH:{rel_str}"
