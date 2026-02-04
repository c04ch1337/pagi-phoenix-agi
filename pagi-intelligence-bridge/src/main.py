"""FastAPI entrypoint for pagi-intelligence-bridge (sidecar to Rust orchestrator)."""

from fastapi import FastAPI

app = FastAPI(title="pagi-intelligence-bridge", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "pagi-intelligence-bridge"}
