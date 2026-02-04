"""FastAPI entrypoint for pagi-intelligence-bridge (sidecar to Rust orchestrator)."""

import traceback

from fastapi import FastAPI

from .recursive_loop import (
    MAX_RECURSION_DEPTH,
    RLMQuery,
    RLMSummary,
    _report_self_heal,
    recursive_loop,
)

app = FastAPI(title="pagi-intelligence-bridge", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {
        "status": "operational",
        "service": "pagi-intelligence-bridge",
        "depth_cap": MAX_RECURSION_DEPTH,
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
