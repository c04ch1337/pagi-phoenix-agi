"""L5 Procedural Skill: save_skill

Write code into the L5 skills registry.

NOTE: This is a *library* skill stub. It does not perform validation or execution.
Validation/sandboxing belongs in the Rust-mediated execution layer when enabled.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class SaveSkillParams(BaseModel):
    filename: str
    code: str


def run(params: SaveSkillParams) -> str:
    skills_dir = Path(__file__).resolve().parent

    # Basic path hardening; final allow-listing/sandboxing belongs to the executor.
    safe_name = (
        params.filename.strip()
        .replace("..", "")
        .replace("/", "_")
        .replace("\\", "_")
    )
    if not safe_name.endswith(".py"):
        safe_name += ".py"

    target = skills_dir / safe_name
    try:
        target.write_text(params.code, encoding="utf-8")
        return f"[save_skill] Saved → {target.name}"
    except Exception as e:
        return f"[save_skill] Write failed: {type(e).__name__}: {e}"

