# Backend Integration Guide — Bare Metal AGI Desktop App

**Audience:** Frontend/Desktop developers and system integrators.  
**Scope:** API/IPC schema, WebSocket events, memory layers, and deployment.  
**Contract:** See [Boilerplate-Contract.md](./Boilerplate-Contract.md) for formal request/response and event types.

---

## 1. Overview

The Phoenix AGI stack consists of:

| Component | Role | Transport | Default Endpoint |
|-----------|------|-----------|------------------|
| **Rust Core (pagi-core-orchestrator)** | Memory, safety, actions, patches | gRPC | `[::1]:50051` |
| **Python Bridge (pagi-intelligence-bridge)** | RLM, skills, HTTP API, WebSocket | HTTP/WS | `127.0.0.1:8000` |
| **Qdrant** | L4 semantic vectors (8 Knowledge Bases) | HTTP | `http://localhost:6334` |

- **Bare Metal:** Run orchestrator and bridge natively; Qdrant optional via `PAGI_DISABLE_QDRANT=true` for loop/action testing.
- **Optional deployment:** Docker Compose is provided for convenience only; see [§5](#5-optional-docker-compose-deployment).

---

## 2. API/IPC Schema for the 8 Knowledge Bases

### 2.1 Knowledge Base Identifiers

All 8 KBs use **string names**; L4 semantic storage is backed by Qdrant collections of the same name.

| KB Name | Purpose |
|---------|--------|
| `kb_core` | Core docs (e.g. ARCHITECTURE.md, README.md) |
| `kb_skills` | Skill metadata and procedural traces |
| `kb_1` … `kb_6` | Domain-specific or vertical KBs (reserved) |

**Contract:** Only these 8 names are valid for `kb_name` in SemanticSearch and UpsertVectors. See [Boilerplate-Contract.md](./Boilerplate-Contract.md) for `KnowledgeBaseName` enum/union.

### 2.2 Semantic Search (gRPC → Rust)

- **Service:** `pagi.Pagi` / `SemanticSearch`
- **Request:** `SearchRequest`
  - `query`: string (human-readable; logging / future server-side embed)
  - `kb_name`: string (one of the 8 KB names)
  - `limit`: uint32 (1–100, clamped server-side)
  - `query_vector`: repeated float (optional; client-provided embedding, length = `PAGI_EMBEDDING_DIM`, default 1536)
- **Response:** `SearchResponse`
  - `hits`: array of `SearchHit`: `document_id`, `score`, `content_snippet`

**IPC path:** Frontend → (optional) Python bridge for embedding → gRPC to Rust → Rust MemoryManager → Qdrant. When Qdrant is disabled, the orchestrator returns empty hits.

### 2.3 Upsert Vectors (gRPC → Rust)

- **Service:** `pagi.Pagi` / `UpsertVectors`
- **Request:** `UpsertRequest`
  - `kb_name`: string (one of the 8 KB names)
  - `points`: array of `VectorPoint`: `id`, `vector` (float[]), `payload` (map<string, string>)
- **Response:** `UpsertResponse`
  - `success`: bool
  - `upserted_count`: uint32

**Payload conventions:** Include `content` or `snippet` for snippet display in search results. Other keys (e.g. `source`, `skill_id`) are storage-specific.

### 2.4 HTTP REST (Python Bridge) — KB-related

The bridge exposes RLM and health; it does **not** expose direct KB CRUD. KB access is via gRPC (orchestrator) only. For indexing flows (e.g. “index ARCHITECTURE.md into kb_core”), the bridge typically:

1. Embeds text (e.g. via `embed_and_upsert`).
2. Calls orchestrator `UpsertVectors` with `kb_name` and points.

So the **API/IPC schema for the 8 KBs** is defined by the gRPC `SemanticSearch` and `UpsertVectors` methods and the 8 `kb_name` values above; the bridge is a client of the orchestrator for KB I/O.

---

## 3. WebSocket Events for Real-Time Agentic Reasoning

For a desktop app, real-time updates are delivered over **WebSocket** from the Python bridge (or a thin proxy in front of it). Events are JSON; type field aligns with [Boilerplate-Contract.md](./Boilerplate-Contract.md) `AgentEventKind`.

### 3.1 Endpoint and Connection

- **URL:** `ws://127.0.0.1:8000/ws/agent` (same host/port as FastAPI; port from `PAGI_HTTP_PORT`).
- **Protocol:** JSON text frames; each message is an **AgentEvent** object with a required `event` (or `type`) field and payload.

### 3.2 Event Types (Schema)

| Event | Description | Typical payload |
|-------|-------------|-----------------|
| `session_started` | RLM session began | `session_id`, `query`, `depth` |
| `thought` | Reasoning step (think) | `reasoning_id`, `depth`, `thought`, `timestamp` |
| `action_planned` | Skill selected, not yet executed | `reasoning_id`, `skill_name`, `params`, `depth` |
| `action_started` | Skill execution started | `reasoning_id`, `skill_name`, `timestamp` |
| `action_completed` | Skill finished | `reasoning_id`, `skill_name`, `success`, `observation`, `error?` |
| `memory_read` | Short-term read (L1/L2) | `layer`, `key`, `data` (optional) |
| `memory_written` | Short-term write (L1/L2) | `layer`, `key` |
| `search_issued` | Semantic search requested | `reasoning_id`, `kb_name`, `query`, `limit` |
| `search_result` | Semantic search result | `reasoning_id`, `kb_name`, `hits_count`, `top_snippet?` |
| `converged` | RLM step converged | `reasoning_id`, `summary`, `final_summary` |
| `error` | Error in pipeline | `reasoning_id?`, `message`, `component` |
| `session_ended` | Session finished | `session_id`, `converged`, `summary?` |

All events should include a **timestamp** (ISO 8601) and, when in a reasoning run, a **reasoning_id** (UUID) for traceability.

### 3.3 Subscription Model

- Client connects to `/ws/agent`; server may optionally accept `?session_id=...` or a JSON handshake with `session_id` to scope events.
- Server pushes events as they occur (no client polling). Client should handle reconnection and backoff.

### 3.4 Alignment with Backend

- **thought / action_planned / action_started / action_completed:** Map from RLM loop (Think/Act/Observe) and from `ExecuteAction` gRPC.
- **memory_read / memory_written:** Map from `AccessMemory` gRPC (L1/L2).
- **search_issued / search_result:** Map from `SemanticSearch` gRPC and the 8 KBs.
- **converged / session_ended:** Map from `RLMSummary` and multi-turn RLM completion.

The **mock_provider.py** and **types.ts** implement this event set so frontend and backend stay synchronized.

---

## 4. Data Structures: Short-Term and Long-Term Memory

### 4.1 Short-Term Memory (L1–L2)

Implemented in Rust `MemoryManager`; access via gRPC `AccessMemory`.

| Layer | Name | Storage | Key/Value | Notes |
|-------|------|---------|-----------|--------|
| **L1** | Sensory | In-memory (DashMap) | key → raw bytes | Ring-buffer stub; value written as UTF-8 bytes |
| **L2** | Working | In-memory (DashMap) | key → string | Current task, scratch context |

**Request:** `MemoryRequest`: `layer` (1–7), `key`, `value` (optional; if present, write).  
**Response:** `MemoryResponse`: `data` (string; read result), `success` (bool).

L3–L7 are reserved; currently only L1 and L2 are implemented. Unimplemented layers may return empty `data` with `success: true` (no error).

### 4.2 Long-Term Memory (L3–L7) and L4 Semantic (8 KBs)

| Layer | Name | Status | Notes |
|-------|------|--------|--------|
| L3 | Episodic | Stub (SurrealDB path reserved) | Not yet implemented |
| **L4** | **Semantic** | **Active** | Qdrant; 8 collections = 8 KBs; 1536-dim default; cosine distance |
| L5 | Procedural | Stub (skills registry on disk) | Evolution registry / skill traces |
| L6 | Conceptual | Stub | Deferred |
| L7 | Identity | Stub | Deferred |

**L4 (Semantic) details:**

- **Dimensions:** `PAGI_EMBEDDING_DIM` (default 1536).
- **Distance:** Cosine.
- **Collections:** Created on demand by `MemoryManager::init_kbs()` for the 8 KB names.
- **Point payload:** At least `content` or `snippet` (string) for snippet in search; other keys allowed (e.g. `source`, `skill_id`).
- **ID:** String or numeric point ID in Qdrant; returned as `document_id` in `SearchHit`.

### 4.3 Summary Table (Memory Layers)

| Layer | Type | Backing | API | Frontend type |
|-------|------|---------|-----|----------------|
| L1 | Short-term | DashMap (bytes) | AccessMemory(layer=1) | ShortTermMemoryRecord (layer 1) |
| L2 | Short-term | DashMap (string) | AccessMemory(layer=2) | ShortTermMemoryRecord (layer 2) |
| L3 | Long-term | Stub | — | Reserved |
| L4 | Long-term | Qdrant (8 KBs) | SemanticSearch, UpsertVectors | SearchHit[], VectorPoint[] |
| L5–L7 | Long-term | Stubs | — | Reserved |

Data structures for frontend/backend sync are in [Boilerplate-Contract.md](./Boilerplate-Contract.md) and **contract/types.ts**.

---

## 5. Optional Docker Compose Deployment

Bare Metal is the primary target. Docker Compose is provided only as an **optional** deployment method.

- **Location:** Repository root: `docker-compose.yml`.
- **Services:** `qdrant` (L4 semantic store); optional commented stubs for `orchestrator` and `bridge` when images exist.
- **Ports:** Qdrant: 6333 (HTTP), 6334 (gRPC). Map `PAGI_GRPC_PORT`, `PAGI_HTTP_PORT` when adding orchestrator/bridge.
- **Volumes:** `qdrant_storage` for Qdrant persistence.
- **Networking:** Use service name `qdrant` for `PAGI_QDRANT_URI` when running orchestrator in Compose.

See `docker-compose.yml` and `.env.example` for exact ports and env vars. For development and desktop integration, running the orchestrator and bridge natively (and Qdrant locally or disabled) is recommended.

### 5.1 Running the Mock Provider (No Rust/Qdrant)

For frontend/desktop development without the full stack, run the contract-aligned mock backend:

```bash
cd pagi-intelligence-bridge
poetry run uvicorn src.mock_provider:app --host 127.0.0.1 --port 8001
```

Then use `http://127.0.0.1:8001` for REST and `ws://127.0.0.1:8001/ws/agent` for WebSocket events. Keep `contract/types.ts` in sync with the mock API and events.

---

## 6. References

- **Proto definitions:** `pagi-proto/pagi.proto` (gRPC services and messages).
- **Memory implementation:** `pagi-core-orchestrator/src/memory_manager.rs` (L1, L2, L4, 8 KBs).
- **Bridge API:** `pagi-intelligence-bridge/src/main.py` (HTTP); WebSocket and mock: `mock_provider.py`.
- **Formal contract:** [Boilerplate-Contract.md](./Boilerplate-Contract.md).
- **Shared types (frontend):** `contract/types.ts`.
