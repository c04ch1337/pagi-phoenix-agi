"""CLI entrypoint for Rust-mediated L5 dispatch: python run_skill.py <skill_name> <json_params>.

Run from bridge root (current_dir). Adds src to path and invokes skills.<skill>.run(Params).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Bridge root = parent of scripts/
BRIDGE_ROOT = Path(__file__).resolve().parent.parent
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

# Import from src/skills (path is bridge_root/src)
SRC = BRIDGE_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _params_class_name(skill_name: str) -> str:
    return "".join(w.capitalize() for w in skill_name.split("_")) + "Params"


def main() -> None:
    if len(sys.argv) < 3:
        print("[run_skill] usage: python run_skill.py <skill_name> <json_params>", file=sys.stderr)
        sys.exit(1)
    skill_name = sys.argv[1]
    params_json = sys.argv[2]

    try:
        mod = __import__(f"skills.{skill_name}", fromlist=["run"])
        run_fn = getattr(mod, "run", None)
        if run_fn is None:
            print("[run_skill] Skill missing run()", file=sys.stderr)
            sys.exit(1)
        params_cls = getattr(mod, _params_class_name(skill_name), None)
        if params_cls is None:
            for cand in (
                "PeekFileParams",
                "SaveSkillParams",
                "ExecuteSkillParams",
                "ListDirParams",
                "ReadEntireFileSafeParams",
                "WriteFileSafeParams",
                "ListFilesRecursiveParams",
                "AnalyzeCodeParams",
                "EvolveSkillFromPatchParams",
                "GenerateNewSkillParams",
            ):
                if hasattr(mod, cand):
                    params_cls = getattr(mod, cand)
                    break
        if params_cls is None:
            print("[run_skill] Params model not found", file=sys.stderr)
            sys.exit(1)
        params = params_cls.model_validate(json.loads(params_json))
        result = run_fn(params)
        print(result)
    except Exception as e:
        print(f"[run_skill] Error: {e!s}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
