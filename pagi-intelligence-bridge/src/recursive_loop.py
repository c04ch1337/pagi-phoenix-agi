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
import logging
from datetime import datetime
from itertools import islice
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


def _allow_real_dispatch() -> bool:
    """When true, bridge sends timeout_ms (and optional allow_list_hash) for Rust-mediated execution."""
    return _env_truthy("PAGI_ALLOW_REAL_DISPATCH", default=False)


def _vertical_use_case() -> str:
    """Configurable vertical. Supported: research (self-patch), codegen (AI codegen), code_review (analyze → run_tests → save to reviewed/). Enables vertical-specific prompts and synthesis hooks."""
    return (os.environ.get("PAGI_VERTICAL_USE_CASE") or "").strip().lower()


def _auto_evolve_enabled() -> bool:
    return _env_truthy("PAGI_AUTO_EVOLVE_SKILLS", default=False)


def _allow_self_heal_grpc() -> bool:
    """When true, bridge calls orchestrator ProposePatch/ApplyPatch via gRPC on error (gated for safety)."""
    return _env_truthy("PAGI_ALLOW_SELF_HEAL_GRPC", default=False)


def _local_dispatch_allow_list() -> set[str]:
    # Minimal surface: allow-listed L5 stubs; execute_skill enables chaining; list_dir/list_files_recursive for discovery; analyze_code for RCA; evolve_skill_from_patch for auto-evolve; search_codebase for pattern search; run_tests for pytest/cargo.
    return {"peek_file", "save_skill", "execute_skill", "list_dir", "read_entire_file_safe", "write_file_safe", "list_files_recursive", "analyze_code", "evolve_skill_from_patch", "search_codebase", "run_tests", "run_python_code_safe"}


_skill_module_cache: dict[str, tuple[float, Any]] = {}


def _load_local_skill_module(skill_name: str):
    skill_path = Path(__file__).resolve().parent / "skills" / f"{skill_name}.py"
    if not skill_path.exists():
        raise FileNotFoundError(f"Local skill not found: {skill_name}")

    # Hot path optimization: cache imported modules by mtime to avoid repeated disk I/O + import work.
    # Disable with PAGI_DISABLE_SKILL_IMPORT_CACHE=true for rapid iteration.
    if not _env_truthy("PAGI_DISABLE_SKILL_IMPORT_CACHE", default=False):
        try:
            mtime = skill_path.stat().st_mtime
            cached = _skill_module_cache.get(skill_name)
            if cached is not None and cached[0] == mtime:
                return cached[1]
        except OSError:
            # Fall through to uncached import.
            pass

    spec = importlib.util.spec_from_file_location(skill_name, skill_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Invalid skill module: {skill_name}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    if not _env_truthy("PAGI_DISABLE_SKILL_IMPORT_CACHE", default=False):
        try:
            _skill_module_cache[skill_name] = (skill_path.stat().st_mtime, mod)
        except OSError:
            pass
    return mod


def _params_class_name(skill_name: str) -> str:
    """e.g. peek_file -> PeekFileParams, save_skill -> SaveSkillParams."""
    return "".join(w.capitalize() for w in skill_name.split("_")) + "Params"


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
        params_cls = getattr(mod, _params_class_name(action.skill_name), None)
        if params_cls is None:
            # Back-compat with earlier hard-coded candidates.
            for cand in ("PeekFileParams", "SaveSkillParams", "ExecuteSkillParams", "ListDirParams", "ReadEntireFileSafeParams", "WriteFileSafeParams", "ListFilesRecursiveParams", "AnalyzeCodeParams", "EvolveSkillFromPatchParams", "SearchCodebaseParams", "RunTestsParams", "RunPythonCodeSafeParams"):
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


_actions_logger: logging.Logger | None = None
_actions_logger_path: str | None = None


def _log_action(line: str) -> None:
    """Append to actions log with a cached FileHandler (avoids per-call open/close)."""
    path = _actions_log_path()
    if not path:
        return

    global _actions_logger, _actions_logger_path
    try:
        if _actions_logger is None or _actions_logger_path != path:
            logger = logging.getLogger("pagi.actions")
            logger.setLevel(logging.INFO)
            logger.propagate = False
            # Replace handlers if log path changed.
            for h in list(logger.handlers):
                logger.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
            _actions_logger = logger
            _actions_logger_path = path
        _actions_logger.info(line.rstrip())
    except Exception:
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


class SynthesisAction(BaseModel):
    """Optional post-synthesis side-effect hook (e.g., trigger auto-evolve)."""

    name: str
    params: dict[str, Any] = Field(default_factory=dict)


def _skills_dir() -> Path:
    """Skills directory next to this package (L5 procedural registry)."""
    return Path(__file__).resolve().parent / "skills"


def peek_file(file_path: str, start: int = 0, end: int = 100) -> str:
    """Read a snippet of a file; generic peek for large-file analysis. Caller must pass safe path."""
    path = Path(file_path).resolve()
    if not path.exists() or not path.is_file():
        return ""
    try:
        # Avoid reading the entire file into memory.
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            snippet_lines = list(islice(f, start, end))
    except OSError:
        return ""
    return "".join(snippet_lines)


def _report_self_heal(error_trace: str, component: str) -> None:
    """Report error to Rust Watchdog for ProposePatch. When PAGI_ALLOW_SELF_HEAL_GRPC=true, calls gRPC ProposePatch then optional ApplyPatch."""
    log_path = os.environ.get("PAGI_SELF_HEAL_LOG")

    if _allow_self_heal_grpc():
        try:
            stub = _get_grpc_stub()
            req = pagi_pb2.PatchRequest(error_trace=error_trace, component=component)
            propose_resp = stub.ProposePatch(req, timeout=10.0)
            obs_lines = [f"ProposePatch: patch_id={propose_resp.patch_id!r} requires_hitl={propose_resp.requires_hitl}"]
            if not propose_resp.requires_hitl:
                apply_req = pagi_pb2.ApplyRequest(
                    patch_id=propose_resp.patch_id,
                    approved=True,
                    component=component,
                    requires_hitl=propose_resp.requires_hitl,
                )
                apply_resp = stub.ApplyPatch(apply_req, timeout=10.0)
                obs_lines.append(f"ApplyPatch: success={apply_resp.success} commit_hash={apply_resp.commit_hash!r}")
            if log_path:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write("Self-heal reported (gRPC)\n")
                    f.write(f"[{component}] {error_trace[:2000]}\n")
                    f.write("\n".join(obs_lines) + "\n")
        except Exception as e:
            if log_path:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write("Self-heal reported (gRPC failed)\n")
                    f.write(f"[{component}] {error_trace[:2000]}\n")
                    f.write(f"grpc_error: {e!s}\n")

    elif log_path:
        with open(log_path, "a", encoding="utf-8") as f:
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
    if _env_truthy("PAGI_VERBOSE_ACTIONS", default=True):
        print(msg)
    _log_action(msg)

    # Prefer Rust-mediated execution to preserve polyglot hierarchy + stable schema.
    if _actions_via_grpc():
        try:
            stub = _get_grpc_stub()
            req_kw: dict = {
                "skill_name": skill,
                "params": {k: str(v) for k, v in params.items()},
                "depth": depth,
                "reasoning_id": reasoning_id,
                "mock_mode": mock_mode,
            }
            if _allow_real_dispatch():
                req_kw["timeout_ms"] = 10000
            req = pagi_pb2.ActionRequest(**req_kw)
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
            # Prefer skill_name + params for chaining (e.g. execute peek_file with path)
            skill_name = str(params.get("skill_name") or (params.get("filename") or "").removesuffix(".py") or "")
            inner = params.get("params") if isinstance(params.get("params"), dict) else {}
            if not skill_name:
                return ("[execute_skill] Missing skill_name or filename", False, "")
            mod = _load_local_skill_module("execute_skill")
            exec_params_cls = getattr(mod, "ExecuteSkillParams", None)
            if exec_params_cls is None:
                return ("[execute_skill] ExecuteSkillParams not found", False, "")
            exec_params = exec_params_cls(skill_name=skill_name, params=inner)
            out = mod.run(exec_params)
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
    # Optional cap for multi-turn context accumulation (character-based).
    max_chars = os.environ.get("PAGI_MULTI_TURN_CONTEXT_MAX_CHARS") or os.environ.get("PAGI_MULTI_TURN_CONTEXT_MAX_TOKENS")
    if max_chars is not None:
        try:
            cap = int(max_chars)
            if len(context) > cap:
                context = context[-cap:]
        except ValueError:
            pass

    mock_mode = _mock_mode()
    allow_outbound = _env_truthy("PAGI_ALLOW_OUTBOUND", default=False)
    enforce_structured = _env_truthy("PAGI_ENFORCE_STRUCTURED", default=True)

    # Phase 3 MockMode: deterministic chain testing without outbound calls.
    if mock_mode:
        rid = str(uuid.uuid4())
        action = ActionSpec(
            skill_name="mock_skill",
            params={"query": query.query, "depth": query.depth, "reasoning_id": rid},
        )
        obs, ok, err = _execute_action(action, depth=query.depth, reasoning_id=rid, mock_mode=True)
        summary = f"MockMode thought: planned={action.skill_name}; ok={ok}; err={err}; {obs}"
        return RLMSummary(summary=summary, converged=True)

    # Structured JSON enforcement (no outbound by default):
    # - If PAGI_RLM_STUB_JSON is set, parse and act on it.
    # - If PAGI_ALLOW_OUTBOUND=true and litellm is available, request a structured JSON response.
    stub = _stub_llm_raw_response()
    if enforce_structured and (stub is not None or (allow_outbound and litellm is not None)):
        try:
            if stub is not None:
                raw = stub
            else:
                system_prompt = os.environ.get(
                    "PAGI_SYSTEM_PROMPT",
                    "Respond ONLY as JSON: {thought: string, action?: {skill_name, params}, observation?: string, is_final: bool}",
                )
                if _vertical_use_case() == "research":
                    system_prompt = system_prompt + " Prioritize self-patch for errors: RCA → propose code → save to L5."
                elif _vertical_use_case() == "codegen":
                    system_prompt = system_prompt + " Prioritize generating code (snippets, tests, refactors). Always end with action: write_file_safe to codegen_output/<filename>"
                elif _vertical_use_case() == "code_review":
                    system_prompt = system_prompt + " Prioritize code review: analyze for issues, propose fixes, run_tests, save reviewed code."
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
                    mock_mode=mock_mode,
                )
                context += f"\nObservation: {obs}"
                _log_action(f"OBSERVATION: ok={ok} err={err} obs={obs[:200]}")

            if parsed.is_final:
                summary = parsed.thought
                # Vertical: codegen — when converged, force write_file_safe to codegen_output/<timestamp>.py with generated code from thought (gated by dispatch).
                if _vertical_use_case() == "codegen" and (_allow_local_dispatch() or _actions_via_grpc()):
                    codegen_dir = os.environ.get("PAGI_CODEGEN_OUTPUT_DIR", "codegen_output")
                    root = os.environ.get("PAGI_PROJECT_ROOT", ".")
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    codegen_path = str(Path(root) / codegen_dir / f"{ts}.py")
                    codegen_action = ActionSpec(
                        skill_name="write_file_safe",
                        params={
                            "path": codegen_path,
                            "content": f"# Codegen vertical\n{parsed.thought}",
                            "overwrite": True,
                        },
                    )
                    rid = str(uuid.uuid4())
                    obs, ok, err = _execute_action(codegen_action, depth=query.depth, reasoning_id=rid, mock_mode=False)
                    summary = f"{summary}\nCodegen write: ok={ok} err={err}; obs={obs[:200]}"
                # Vertical: code_review — when converged, force chain analyze_code → run_tests → write_file_safe to reviewed/<filename> (gated by dispatch).
                elif _vertical_use_case() == "code_review" and (_allow_local_dispatch() or _actions_via_grpc()):
                    root = Path(os.environ.get("PAGI_PROJECT_ROOT", ".")).resolve()
                    review_dir = os.environ.get("PAGI_CODE_REVIEW_OUTPUT_DIR", "reviewed")
                    out_dir = root / review_dir
                    out_dir.mkdir(parents=True, exist_ok=True)
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"reviewed_{ts}.py"
                    review_path = str(out_dir / filename)
                    code_for_analysis = parsed.thought
                    if "code:" in context:
                        idx = context.find("code:")
                        snippet = context[idx + 5 :].strip()
                        if "\n\n" in snippet:
                            snippet = snippet.split("\n\n")[0]
                        code_for_analysis = snippet[:4096] if snippet else parsed.thought
                    analyze_action = ActionSpec(
                        skill_name="analyze_code",
                        params={"code": code_for_analysis[:4096], "language": "python", "max_length": 4096},
                    )
                    rid = str(uuid.uuid4())
                    analyze_obs, _, _ = _execute_action(analyze_action, depth=query.depth, reasoning_id=rid, mock_mode=False)
                    test_dir = str(root)
                    run_tests_action = ActionSpec(
                        skill_name="run_tests",
                        params={"dir": test_dir, "type": "python", "timeout_sec": 30},
                    )
                    rid = str(uuid.uuid4())
                    test_obs, _, _ = _execute_action(run_tests_action, depth=query.depth, reasoning_id=rid, mock_mode=False)
                    review_content = f"# Code review {ts}\n# RCA: {analyze_obs[:500]}\n\n{parsed.thought}"
                    write_action = ActionSpec(
                        skill_name="write_file_safe",
                        params={"path": review_path, "content": review_content, "overwrite": True},
                    )
                    rid = str(uuid.uuid4())
                    write_obs, write_ok, write_err = _execute_action(write_action, depth=query.depth, reasoning_id=rid, mock_mode=False)
                    summary = f"{summary}\nCode review: analyze ok; run_tests: {test_obs[:200]}; write: ok={write_ok} err={write_err}; obs={write_obs[:200]}"
                # Vertical: self-patch codegen — when converged and query asks for self-patch, write fix to L5 (gated by dispatch).
                # Optional auto_evolve: when PAGI_AUTO_EVOLVE_SKILLS=true, Watchdog triggers evolve_skill_from_patch after successful python_skill apply.
                elif "self-patch" in query.query.lower() and _vertical_use_case() == "research":
                    if _allow_local_dispatch() or _actions_via_grpc():
                        fix_content = (context + "\n" + parsed.thought)[:4000]
                        root = os.environ.get("PAGI_PROJECT_ROOT", ".")
                        patch_dir = os.environ.get("PAGI_SELF_PATCH_DIR", "patches")
                        patch_path = str(Path(root) / patch_dir / "patch_rs.txt")
                        patch_action = ActionSpec(
                            skill_name="write_file_safe",
                            params={
                                "path": patch_path,
                                "content": f"# Proposed fix (RCA)\n{fix_content}",
                                "overwrite": "true",
                            },
                        )
                        rid = str(uuid.uuid4())
                        obs, ok, err = _execute_action(patch_action, depth=query.depth, reasoning_id=rid, mock_mode=False)
                        summary = f"{summary}\nSelf-patch write: ok={ok} err={err}; obs={obs[:200]}"

                # Optional "auto_evolve" action in synthesis when vertical==research and is_final.
                # Emit marker only; evolution is gated and performed by Rust Watchdog after apply/commit.
                if _vertical_use_case() == "research" and _auto_evolve_enabled():
                    try:
                        synth = SynthesisAction(name="auto_evolve", params={"enabled": True})
                        summary = f"{summary}\nSYNTHESIS_ACTION:{synth.model_dump_json()}"
                    except Exception:
                        pass
                return RLMSummary(summary=summary, converged=True)
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

    # Vertical: self-patch codegen — in fallback synthesis, if query asks for self-patch and dispatch allowed, write fix stub.
    summary_final = "Synthesized generic response"
    if converged and "self-patch" in query.query.lower() and _vertical_use_case() == "research":
        if _allow_local_dispatch() or _actions_via_grpc():
            fix_content = (context or "")[:2000]
            root = os.environ.get("PAGI_PROJECT_ROOT", ".")
            patch_dir = os.environ.get("PAGI_SELF_PATCH_DIR", "patches")
            patch_path = str(Path(root) / patch_dir / "patch_rs.txt")
            patch_action = ActionSpec(
                skill_name="write_file_safe",
                params={
                    "path": patch_path,
                    "content": f"# Proposed fix (RCA)\n{fix_content}",
                    "overwrite": "true",
                },
            )
            rid = str(uuid.uuid4())
            obs, ok, err = _execute_action(patch_action, depth=query.depth, reasoning_id=rid, mock_mode=False)
            summary_final = f"Self-patch synthesis: ok={ok}; obs={obs[:200]}"

    return RLMSummary(summary=summary_final, converged=converged)


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
