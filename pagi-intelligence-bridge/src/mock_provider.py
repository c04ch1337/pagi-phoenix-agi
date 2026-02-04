"""
Mock Backend Provider — Contract-aligned API and WebSocket for frontend/desktop integration.

Implements docs/Boilerplate-Contract.md and stays in sync with contract/types.ts.
Use for development and E2E tests without the full Rust orchestrator or Qdrant.

Endpoints:
  - GET  /health
  - POST /api/memory          (AccessMemory equivalent)
  - POST /api/search          (SemanticSearch equivalent)
  - POST /api/upsert          (UpsertVectors equivalent)
  - POST /api/action          (ExecuteAction equivalent)
  - POST /api/rlm             (RLM single step)
  - WS   /ws/agent            (real-time AgentEvent stream)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Contract: 8 KB names (align with contract/types.ts KnowledgeBaseName)
# ---------------------------------------------------------------------------

KNOWLEDGE_BASE_NAMES = frozenset({
    "kb_core", "kb_skills", "kb_1", "kb_2", "kb_3", "kb_4", "kb_5", "kb_6",
})

# WebSocket event kinds (align with contract/types.ts AgentEventKind)
AGENT_EVENT_KINDS = frozenset({
    "session_started", "thought", "action_planned", "action_started", "action_completed",
    "memory_read", "memory_written", "search_issued", "search_result", "converged",
    "error", "session_ended",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Request/Response models (Boilerplate Contract §2)
# ---------------------------------------------------------------------------

class MemoryAccessRequest(BaseModel):
    layer: int = Field(ge=1, le=7)
    key: str
    value: str | None = None


class MemoryAccessResponse(BaseModel):
    data: str
    success: bool


class SearchRequest(BaseModel):
    query: str
    kb_name: str
    limit: int = Field(default=10, ge=1, le=100)
    query_vector: list[float] | None = None


class SearchHit(BaseModel):
    document_id: str
    score: float
    content_snippet: str


class SearchResponse(BaseModel):
    hits: list[SearchHit]


class VectorPoint(BaseModel):
    id: str
    vector: list[float]
    payload: dict[str, str] = Field(default_factory=dict)


class UpsertVectorsRequest(BaseModel):
    kb_name: str
    points: list[VectorPoint]


class UpsertVectorsResponse(BaseModel):
    success: bool
    upserted_count: int


class ExecuteActionRequest(BaseModel):
    skill_name: str
    params: dict[str, str] = Field(default_factory=dict)
    depth: int = 0
    reasoning_id: str = ""
    mock_mode: bool = True
    timeout_ms: int | None = None


class ExecuteActionResponse(BaseModel):
    observation: str
    success: bool
    error: str = ""


class RLMRequest(BaseModel):
    query: str
    context: str = ""
    depth: int = Field(default=0, ge=0, le=10)


class RLMResponse(BaseModel):
    summary: str
    converged: bool


# ---------------------------------------------------------------------------
# In-memory stores (mock L1/L2 and L4 KBs)
# ---------------------------------------------------------------------------

# L1: key -> bytes (stored as base64 or we keep as str for simplicity)
_l1: dict[str, bytes] = {}
# L2: key -> string
_l2: dict[str, str] = {}
# L4: kb_name -> list of { id, vector, payload }; mock search uses simple string match
_kbs: dict[str, list[dict[str, Any]]] = {name: [] for name in KNOWLEDGE_BASE_NAMES}


def _memory_access(layer: int, key: str, value: str | None) -> MemoryAccessResponse:
    if layer == 1:
        if value is not None:
            _l1[key] = value.encode("utf-8")
        data = _l1.get(key, b"").decode("utf-8", errors="replace")
    elif layer == 2:
        if value is not None:
            _l2[key] = value
        data = _l2.get(key, "")
    else:
        data = ""
    return MemoryAccessResponse(data=data, success=True)


def _search(query: str, kb_name: str, limit: int) -> SearchResponse:
    if kb_name not in _kbs:
        return SearchResponse(hits=[])
    points = _kbs[kb_name]
    # Mock: filter by query substring in payload content/snippet
    q = query.lower()
    hits: list[SearchHit] = []
    for i, p in enumerate(points):
        payload = p.get("payload", {})
        content = (payload.get("content") or payload.get("snippet") or "").lower()
        if q in content or not q:
            hits.append(SearchHit(
                document_id=p.get("id", str(i)),
                score=0.9 - i * 0.05,
                content_snippet=(payload.get("content") or payload.get("snippet") or "")[:500],
            ))
        if len(hits) >= limit:
            break
    return SearchResponse(hits=hits[:limit])


def _upsert(kb_name: str, points: list[VectorPoint]) -> UpsertVectorsResponse:
    if kb_name not in _kbs:
        return UpsertVectorsResponse(success=False, upserted_count=0)
    for p in points:
        _kbs[kb_name].append({
            "id": p.id,
            "vector": p.vector,
            "payload": p.payload,
        })
    return UpsertVectorsResponse(success=True, upserted_count=len(points))


# ---------------------------------------------------------------------------
# FastAPI app and routes
# ---------------------------------------------------------------------------

app = FastAPI(
    title="pagi-mock-provider",
    description="Contract-aligned mock backend for AGI desktop integration",
    version="1.0.0",
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "operational",
        "service": "pagi-mock-provider",
        "contract": "Boilerplate-Contract.md",
    }


@app.post("/api/memory", response_model=MemoryAccessResponse)
def api_memory(req: MemoryAccessRequest) -> MemoryAccessResponse:
    return _memory_access(req.layer, req.key, req.value)


@app.post("/api/search", response_model=SearchResponse)
def api_search(req: SearchRequest) -> SearchResponse:
    if req.kb_name not in KNOWLEDGE_BASE_NAMES:
        return SearchResponse(hits=[])
    return _search(req.query, req.kb_name, req.limit)


@app.post("/api/upsert", response_model=UpsertVectorsResponse)
def api_upsert(req: UpsertVectorsRequest) -> UpsertVectorsResponse:
    if req.kb_name not in KNOWLEDGE_BASE_NAMES:
        return UpsertVectorsResponse(success=False, upserted_count=0)
    return _upsert(req.kb_name, req.points)


@app.post("/api/action", response_model=ExecuteActionResponse)
def api_action(req: ExecuteActionRequest) -> ExecuteActionResponse:
    rid = req.reasoning_id or str(uuid.uuid4())
    return ExecuteActionResponse(
        observation=f"Mock observation for skill={req.skill_name} (reasoning_id={rid})",
        success=True,
        error="",
    )


@app.post("/api/rlm", response_model=RLMResponse)
def api_rlm(req: RLMRequest) -> RLMResponse:
    return RLMResponse(
        summary=f"Mock RLM summary for: {req.query[:80]}...",
        converged=req.depth >= 1,
    )


# ---------------------------------------------------------------------------
# WebSocket: /ws/agent — stream AgentEvent JSON (Contract §3)
# ---------------------------------------------------------------------------

def _emit(ws: WebSocket, event: str, payload: dict[str, Any], reasoning_id: str | None = None) -> None:
    msg = {"event": event, "timestamp": _now_iso(), **payload}
    if reasoning_id:
        msg["reasoning_id"] = reasoning_id
    ws.send_text(json.dumps(msg))


@app.websocket("/ws/agent")
async def ws_agent(websocket: WebSocket) -> None:
    await websocket.accept()
    session_id = str(uuid.uuid4())
    reasoning_id = str(uuid.uuid4())

    try:
        # Optional: read first frame as handshake e.g. {"query": "..."}
        try:
            raw = await websocket.receive_text()
            data = json.loads(raw) if raw else {}
            query = data.get("query", "mock query")
            depth = data.get("depth", 0)
        except (WebSocketDisconnect, json.JSONDecodeError):
            query = "mock query"
            depth = 0

        _emit(websocket, "session_started", {"session_id": session_id, "query": query, "depth": depth})

        _emit(websocket, "thought", {"thought": "Mock reasoning step.", "depth": depth}, reasoning_id)
        _emit(websocket, "action_planned", {
            "skill_name": "peek_file", "params": {"path": "README.md"}, "depth": depth,
        }, reasoning_id)
        _emit(websocket, "action_started", {"skill_name": "peek_file"}, reasoning_id)
        _emit(websocket, "action_completed", {
            "skill_name": "peek_file", "success": True, "observation": "Mock file content.",
        }, reasoning_id)
        _emit(websocket, "search_issued", {"kb_name": "kb_core", "query": query, "limit": 5}, reasoning_id)
        _emit(websocket, "search_result", {"kb_name": "kb_core", "hits_count": 0, "top_snippet": None}, reasoning_id)
        _emit(websocket, "converged", {"summary": "Mock converged.", "final_summary": "Mock final."}, reasoning_id)
        _emit(websocket, "session_ended", {"session_id": session_id, "converged": True, "summary": "Mock summary."})

        # Keep connection open until client closes (or add heartbeat / timeout)
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            _emit(websocket, "error", {"message": str(e), "component": "mock_provider"}, reasoning_id)
        except Exception:
            pass
