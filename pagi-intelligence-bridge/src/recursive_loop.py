"""MIT RLM core: REPL loop with schema enforcement, depth tracking, delegation, skills registry.

Phase 3 goal: improve observability and mocking fidelity without schema drift.

Key invariants:
- Depth-capped recursion (default 5; env: PAGI_MAX_RECURSION_DEPTH)
- Optional structured JSON loop discipline (Pydantic validation)
- MockMode (env: PAGI_MOCK_MODE=true) to exercise planning/action chaining without outbound calls
"""

from __future__ import annotations

import os
import subprocess
import importlib.util
import traceback
import json
import re
import uuid
from pathlib import Path

from typing import Any, Optional

from pydantic import BaseModel, Field

import grpc

from .pagi_pb import pagi_pb2, pagi_pb2_grpc

try:
    import litellm
except ImportError:
    litellm = None

# Max recursion depth; aligns with Rust SafetyGovernor. Override via PAGI_MAX_RECURSION_DEPTH.
MAX_RECURSION_DEPTH = int(os.environ.get("PAGI_MAX_RECURSION_DEPTH", "5"))
PEEK_MAX_CHARS = int(os.environ.get("PAGI_PEEK_MAX_CHARS", "2000"))


def _env_truthy(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


def _mock_mode() -> bool:
    return _env_truthy("PAGI_MOCK_MODE", default=False)


def _actions_via_grpc() -> bool:
    return _env_truthy("PAGI_ACTIONS_VIA_GRPC", default=False)


def _allow_local_dispatch() -> bool:
    return _env_truthy("PAGI_ALLOW_LOCAL_DISPATCH", default=False)


def _local_dispatch_allow_list() -> set[str]:
    # Minimal surface: only allow the initial L5 stubs.
    return {"peek_file", "save_skill"}


def _load_local_skill_module(skill_name: str):
    skill_path = Path(__file__).resolve().parent / "skills" / f"{skill_name}.py"
    if not skill_path.exists():
        raise FileNotFoundError(f"Local skill not found: {skill_name}")
    spec = importlib.util.spec_from_file_location(skill_name, skill_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Invalid skill module: {skill_name}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _execute_action_locally(action: ActionSpec) -> tuple[str, bool, str]:
    """Execute allow-listed L5 skills in-process (gated).

    This is intended for verifiable local testing without Rust/gRPC, not for unrestricted execution.
    """
    if not _allow_local_dispatch():
        return ("Local dispatch disabled", False, "local_dispatch_disabled")
    if action.skill_name not in _local_dispatch_allow_list():
        return ("Local dispatch denied", False, "local_dispatch_denied")

    try:
        mod = _load_local_skill_module(action.skill_name)
        run_fn = getattr(mod, "run", None)
        if run_fn is None:
            return ("Skill missing run()", False, "missing_run")

        # Convention: <SkillName>Params (e.g., PeekFileParams, SaveSkillParams)
        params_obj = action.params or {}
        params_cls = None
        for cand in ("PeekFileParams", "SaveSkillParams"):
            if hasattr(mod, cand):
                params_cls = getattr(mod, cand)
                break
        if params_cls is None:
            return ("Skill params model not found", False, "missing_params_model")

        params = params_cls.model_validate(params_obj)
        obs = run_fn(params)
        return (str(obs), True, "")
    except Exception as e:
        return ("Action failed", False, f"local_error:{e!s}")


def _grpc_addr() -> str:
    # Keep consistent with Rust default in [`pagi-core-orchestrator/src/main.rs`](pagi-core-orchestrator/src/main.rs:123)
    return os.environ.get("PAGI_GRPC_ADDR") or "[::1]:50051"


_grpc_channel: grpc.Channel | None = None
_grpc_stub: pagi_pb2_grpc.PagiStub | None = None


def _get_grpc_stub() -> pagi_pb2_grpc.PagiStub:
    global _grpc_channel, _grpc_stub
    if _grpc_stub is not None:
        return _grpc_stub
    _grpc_channel = grpc.insecure_channel(_grpc_addr())
    _grpc_stub = pagi_pb2_grpc.PagiStub(_grpc_channel)
    return _grpc_stub


def _actions_log_path() -> Optional[str]:
    # Accept both names to avoid churn across configs.
    return os.environ.get("PAGI_AGENT_ACTIONS_LOG") or os.environ.get("PAGI_ACTIONS_LOG")


def _log_action(line: str) -> None:
    path = _actions_log_path()
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
    except OSError:
        # Observability should not crash the loop.
        return


class RLMQuery(BaseModel):
    """Strict-typed input for recursive reasoning."""

    query: str
    context: str = ""
    depth: int = Field(default=0, ge=0, le=MAX_RECURSION_DEPTH)


class RLMSummary(BaseModel):
    """Output of one RLM step; converged signals synthesis done."""

    summary: str
    converged: bool


class ActionSpec(BaseModel):
    """Stable action schema used by the reasoning loop."""

    skill_name: str
    params: dict[str, Any] = Field(default_factory=dict)


class RLMStructuredResponse(BaseModel):
    """Structured response format for RLM steps (Think/Act/Observe)."""

    thought: str
    action: Optional[ActionSpec] = None
    observation: Optional[str] = None
    is_final: bool = False


def _skills_dir() -> Path:
    """Skills directory next to this package (L5 procedural registry)."""
    return Path(__file__).resolve().parent / "skills"


def peek_file(file_path: str, start: int = 0, end: int = 100) -> str:
    """Read a snippet of a file; generic peek for large-file analysis. Caller must pass safe path."""
    path = Path(file_path).resolve()
    if not path.exists() or not path.is_file():
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return ""
    snippet = lines[start:end]
    return "".join(snippet)


def _report_self_heal(error_trace: str, component: str) -> None:
    """Report error to Rust Watchdog for ProposePatch. Stub: gRPC client when integrated."""
    # TODO: Call orchestrator ProposePatch(error_trace=..., component=component) via gRPC
    # Then optionally ApplyPatch if not requires_hitl (python_skill).
    if os.environ.get("PAGI_SELF_HEAL_LOG"):
        with open(os.environ["PAGI_SELF_HEAL_LOG"], "a", encoding="utf-8") as f:
            f.write("Self-heal reported\n")
            f.write(f"[{component}] {error_trace}\n")


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _strip_json_fences(text: str) -> str:
    return _JSON_FENCE_RE.sub("", text.strip())


def _parse_structured_response(raw: str) -> RLMStructuredResponse:
    cleaned = _strip_json_fences(raw)
    data = json.loads(cleaned)
    return RLMStructuredResponse.model_validate(data)


def _stub_llm_raw_response() -> Optional[str]:
    """Testing hook: provide an assistant JSON blob without outbound calls."""
    return os.environ.get("PAGI_RLM_STUB_JSON")


def _execute_action(
    action: ActionSpec,
    *,
    depth: int,
    reasoning_id: str,
    mock_mode: bool,
) -> tuple[str, bool, str]:
    """Execute an action via Rust gRPC (preferred) or locally (Phase 3)."""
    skill = action.skill_name
    params = action.params or {}

    msg = f"EXECUTING: {skill} mock={mock_mode} reasoning_id={reasoning_id}"
    print(msg)
    _log_action(msg)

    # Prefer Rust-mediated execution to preserve polyglot hierarchy + stable schema.
    if _actions_via_grpc():
        try:
            stub = _get_grpc_stub()
            req = pagi_pb2.ActionRequest(
                skill_name=skill,
                params={k: str(v) for k, v in params.items()},
                depth=depth,
                reasoning_id=reasoning_id,
                mock_mode=mock_mode,
            )
            resp = stub.ExecuteAction(req, timeout=10.0)
            if resp.success:
                return (resp.observation, True, "")
            return (resp.observation, False, resp.error)
        except Exception as e:
            return ("Action failed", False, f"grpc_error:{e!s}")

    # Optional local dispatch (gated + allow-listed).
    if _allow_local_dispatch():
        return _execute_action_locally(action)

    if mock_mode:
        return (f"Observation: mock executed skill={skill}", True, "")

    # Bare-metal local skills only (no outbound). gRPC wiring to Rust comes later.
    try:
        if skill == "peek_file":
            path = str(params.get("path") or params.get("file_path") or "")
            start = int(params.get("start", 0))
            end = int(params.get("end", 100))
            snippet = peek_file(path, start=start, end=end)
            return (snippet[:PEEK_MAX_CHARS], True, "")

        if skill == "save_skill":
            filename = str(params.get("filename") or "new_skill.py")
            code = str(params.get("code") or "# empty skill\n")
            save_skill(filename, code)
            return (f"Saved skill: {filename}", True, "")

        if skill == "execute_skill":
            filename = str(params.get("filename") or "")
            out = execute_skill(filename)
            return (out, True, "")

        return ("Unknown skill", False, f"unknown_skill:{skill}")
    except Exception as e:
        return ("Action failed", False, str(e))


def recursive_loop(query: RLMQuery) -> RLMSummary:
    """Peek / delegate / synthesize loop. Circuit breaker at depth > 5."""
    try:
        return _recursive_loop_impl(query)
    except Exception:
        error_trace = traceback.format_exc()
        _report_self_heal(error_trace, "python_skill")
        return RLMSummary(
            summary=f"Self-heal reported: {error_trace[:500]}",
            converged=False,
        )


def _recursive_loop_impl(query: RLMQuery) -> RLMSummary:
    """Inner implementation; exceptions bubble for self-heal capture."""
    if query.depth >= MAX_RECURSION_DEPTH:
        return RLMSummary(summary="Depth limit reached", converged=False)

    context = query.context

    # Phase 3 MockMode: deterministic chain testing without outbound calls.
    if _mock_mode():
            rid = str(uuid.uuid4())
            action = ActionSpec(skill_name="mock_skill", params={"query": query.query, "depth": query.depth, "reasoning_id": rid})
            obs, ok, err = _execute_action(action, depth=query.depth, reasoning_id=rid, mock_mode=True)
            summary = f"MockMode thought: planned={action.skill_name}; ok={ok}; err={err}; {obs}"
            return RLMSummary(summary=summary, converged=True)

    # Structured JSON enforcement (no outbound by default):
    # - If PAGI_RLM_STUB_JSON is set, parse and act on it.
    # - If PAGI_ALLOW_OUTBOUND=true and litellm is available, request a structured JSON response.
    stub = _stub_llm_raw_response()
    allow_outbound = _env_truthy("PAGI_ALLOW_OUTBOUND", default=False)
    enforce_structured = _env_truthy("PAGI_ENFORCE_STRUCTURED", default=True)
    if enforce_structured and (stub is not None or (allow_outbound and litellm is not None)):
        try:
            if stub is not None:
                raw = stub
            else:
                system_prompt = os.environ.get(
                    "PAGI_SYSTEM_PROMPT",
                    "Respond ONLY as JSON: {thought: string, action?: {skill_name, params}, observation?: string, is_final: bool}",
                )
                resp = litellm.completion(
                    model=os.environ.get("PAGI_OPENROUTER_MODEL", "openrouter/auto"),
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": query.model_dump_json()},
                    ],
                )
                raw = resp.choices[0].message.content or "{}"

            parsed = _parse_structured_response(raw)
            _log_action(f"THOUGHT: {parsed.thought}")

            if parsed.action is not None:
                rid = str(parsed.action.params.get("reasoning_id") or "") if parsed.action.params else ""
                rid = rid or str(uuid.uuid4())
                obs, ok, err = _execute_action(
                    parsed.action,
                    depth=query.depth,
                    reasoning_id=rid,
                    mock_mode=_mock_mode(),
                )
                context += f"\nObservation: {obs}"
                _log_action(f"OBSERVATION: ok={ok} err={err} obs={obs[:200]}")

            if parsed.is_final:
                return RLMSummary(summary=parsed.thought, converged=True)
            return RLMSummary(summary=parsed.thought, converged=False)
        except Exception as e:
            error_trace = f"Schema enforcement failed: {e!s}"
            _report_self_heal(error_trace, "python_skill")
            return RLMSummary(summary=error_trace, converged=False)

    # Peeking: if context signals large-file, try to peek (generic; verticals override)
    if "large_file" in query.context.lower():
        # Example: context may contain "file:path/to/file.txt" or use placeholder
        if "file:" in query.context:
            part = query.context.split("file:")[-1].split()[0].strip()
            peeked = peek_file(part, 0, 50)
            if peeked:
                context += f"\nPeeked: {peeked[:PEEK_MAX_CHARS]}"

    # Delegation: outbound delegation is disabled unless PAGI_ALLOW_OUTBOUND=true.
    if allow_outbound and "complex" in query.query.lower():
        if litellm is not None:
            try:
                resp = litellm.completion(
                    model=os.environ.get("PAGI_OPENROUTER_MODEL", "openrouter/auto"),
                    messages=[{"role": "user", "content": query.model_dump_json()}],
                )
                sub_summary = resp.choices[0].message.content or ""
                context += f"\nSub-summary: {sub_summary[:PEEK_MAX_CHARS]}"
            except Exception as e:
                context += f"\nSub-error: {e!s}"
        else:
            context += "\nSub-summary: (litellm not available)"

    # Synthesis: generic convergence check (placeholder; verticals override)
    converged = "resolved" in context.lower() or query.depth >= MAX_RECURSION_DEPTH - 1

    # Skill save if validated (L5 traceability)
    if converged and "save_skill" in query.query.lower():
        try:
            save_skill("new_skill.py", "# Generic skill code\nprint('Executed')")
        except ValueError:
            pass

    return RLMSummary(summary="Synthesized generic response", converged=converged)


def save_skill(filename: str, code: str) -> None:
    """Write skill to src/skills and validate by running in subprocess. L5 registry."""
    skills_dir = _skills_dir()
    skills_dir.mkdir(parents=True, exist_ok=True)
    if not filename.endswith(".py"):
        filename = f"{filename}.py"
    path = skills_dir / filename
    path.write_text(code, encoding="utf-8")
    try:
        subprocess.run(
            [os.environ.get("PAGI_PYTHON", "python"), str(path)],
            check=True,
            capture_output=True,
            timeout=10,
            cwd=str(path.parent),
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise ValueError(f"Skill validation failed: {e}") from e


def execute_skill(filename: str) -> str:
    """Load and run a skill module from src/skills. Returns stub result."""
    if not filename.endswith(".py"):
        filename = f"{filename}.py"
    path = _skills_dir() / filename
    if not path.exists():
        raise FileNotFoundError(f"Skill not found: {filename}")
    spec = importlib.util.spec_from_file_location("skill", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Invalid skill module: {filename}")
    skill = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(skill)
    return "Skill executed"
