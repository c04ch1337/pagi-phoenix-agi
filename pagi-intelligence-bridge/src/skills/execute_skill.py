"""L5 Procedural Skill: execute_skill – Run allow-listed skill from registry.

Chaining: peek_file → execute_skill(peek_file, params) → save_skill.
Gated by allow-list in recursive_loop; no broadening of execution surface.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from pydantic import BaseModel, Field


_REGISTRY_DIR = Path(__file__).resolve().parent
_module_cache: dict[str, tuple[float, object]] = {}


class ExecuteSkillParams(BaseModel):
    skill_name: str
    params: dict = Field(default_factory=dict)  # Forwarded to target skill's Params model


def _params_class_name(skill_name: str) -> str:
    """e.g. peek_file -> PeekFileParams, save_skill -> SaveSkillParams."""
    return "".join(w.capitalize() for w in skill_name.split("_")) + "Params"


def run(params: ExecuteSkillParams) -> str:
    skill_path = _REGISTRY_DIR / f"{params.skill_name}.py"
    if not skill_path.exists():
        return f"[execute_skill] Skill not found: {params.skill_name}"

    try:
        # Small perf win: cache imported modules by mtime (same strategy as recursive_loop).
        try:
            mtime = skill_path.stat().st_mtime
            cached = _module_cache.get(params.skill_name)
            if cached is not None and cached[0] == mtime:
                skill_mod = cached[1]
            else:
                spec = importlib.util.spec_from_file_location(params.skill_name, skill_path)
                if spec is None or spec.loader is None:
                    return f"[execute_skill] Invalid module: {params.skill_name}"
                skill_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(skill_mod)
                _module_cache[params.skill_name] = (mtime, skill_mod)
        except OSError:
            spec = importlib.util.spec_from_file_location(params.skill_name, skill_path)
            if spec is None or spec.loader is None:
                return f"[execute_skill] Invalid module: {params.skill_name}"
            skill_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(skill_mod)

        params_cls_name = _params_class_name(params.skill_name)
        params_class = getattr(skill_mod, params_cls_name, None)
        if params_class is None:
            return f"[execute_skill] Params class not found: {params_cls_name}"

        skill_params = params_class.model_validate(params.params)
        run_fn = getattr(skill_mod, "run", None)
        if run_fn is None:
            return f"[execute_skill] Skill missing run(): {params.skill_name}"

        result = run_fn(skill_params)
        return str(result)
    except Exception as e:
        return f"[execute_skill] Execution failed: {type(e).__name__}: {e}"
