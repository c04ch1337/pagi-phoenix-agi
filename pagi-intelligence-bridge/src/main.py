"""FastAPI entrypoint for pagi-intelligence-bridge (sidecar to Rust orchestrator)."""

import os
import traceback

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()  # Load .env from cwd if present (reproducible L5 verification)

from .recursive_loop import (
    MAX_RECURSION_DEPTH,
    RLMQuery,
    RLMSummary,
    _report_self_heal,
    recursive_loop,
)


class RLMMultiTurnRequest(RLMQuery):
    """RLM query with optional max_turns for /rlm-multi-turn."""

    max_turns: int = 5


app = FastAPI(title="pagi-intelligence-bridge", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {
        "status": "operational",
        "service": "pagi-intelligence-bridge",
        "depth_cap": MAX_RECURSION_DEPTH,
    }


@app.get("/health/env")
def health_env() -> dict:
    """Return current PAGI env state for debugging (L5 verification checklist)."""
    return {
        "PAGI_ALLOW_LOCAL_DISPATCH": os.environ.get("PAGI_ALLOW_LOCAL_DISPATCH"),
        "PAGI_MOCK_MODE": os.environ.get("PAGI_MOCK_MODE"),
        "PAGI_ACTIONS_VIA_GRPC": os.environ.get("PAGI_ACTIONS_VIA_GRPC"),
        "PAGI_AGENT_ACTIONS_LOG": os.environ.get("PAGI_AGENT_ACTIONS_LOG"),
        "PAGI_RLM_STUB_JSON": (
            "(set)" if os.environ.get("PAGI_RLM_STUB_JSON") else None
        ),
    }


@app.post("/debug")
def debug_trigger(data: dict) -> dict:
    """Stub to simulate error → self-heal flow; logs to agent_actions.log when PAGI_SELF_HEAL_LOG set."""
    if data.get("trigger_error"):
        try:
            raise ValueError("Simulated error for self-heal test")
        except ValueError:
            error_trace = traceback.format_exc()
            _report_self_heal(error_trace, "python_skill")
            return {"status": "simulated_error_logged", "message": "Self-heal flow triggered; check agent_actions.log"}
    return {"status": "no error"}


@app.post("/rlm", response_model=RLMSummary)
def handle_rlm(query: RLMQuery) -> RLMSummary:
    """Run one RLM step: peek / delegate / synthesize. Delegation guarded by Rust via gRPC in production."""
    return recursive_loop(query)


@app.post("/rlm-multi-turn")
def handle_rlm_multi_turn(body: RLMMultiTurnRequest) -> list[dict]:
    """Run multi-turn RLM: loop recursive_loop, inject summary as context until converged or max_turns. Returns list of RLMSummary dicts."""
    summaries: list[dict] = []
    query = RLMQuery(query=body.query, context=body.context, depth=body.depth)
    for _ in range(body.max_turns):
        out = recursive_loop(query)
        summaries.append(out.model_dump())
        if out.converged:
            break
        query = RLMQuery(
            query=body.query,
            context=(query.context + "\n" + out.summary).strip(),
            depth=query.depth,
        )
    return summaries
