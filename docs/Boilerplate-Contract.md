# Boilerplate Contract — API, Events, and Data Shapes

This document is the **single source of truth** for request/response and WebSocket event shapes. Backend (`mock_provider.py`) and frontend (`contract/types.ts`) MUST stay synchronized with this contract.

---

## 1. Knowledge Base Names (8 KBs)

Valid values for `kb_name` in SemanticSearch and UpsertVectors:

```ts
type KnowledgeBaseName =
  | "kb_core"
  | "kb_skills"
  | "kb_1"
  | "kb_2"
  | "kb_3"
  | "kb_4"
  | "kb_5"
  | "kb_6";
```

**Python:** Use a `Literal` union or a set of allowed strings.  
**Rust:** Already constrained by `init_kbs()` collection names.

---

## 2. gRPC-Derived API Shapes (HTTP/REST Equivalents for Mock)

These align with `pagi.proto`; the mock provider may expose REST or WebSocket equivalents with the same shapes.

### 2.1 Memory (Short-Term L1/L2)

**AccessMemory (read/write)**

- **Request:**
  - `layer`: number (1–7; only 1 and 2 implemented)
  - `key`: string
  - `value`?: string (omit for read, set for write)
- **Response:**
  - `data`: string
  - `success`: boolean

### 2.2 Semantic Search (L4, 8 KBs)

**SemanticSearch**

- **Request:**
  - `query`: string
  - `kb_name`: KnowledgeBaseName
  - `limit`: number (1–100)
  - `query_vector`?: number[] (length = embedding dim, e.g. 1536)
- **Response:**
  - `hits`: Array of:
    - `document_id`: string
    - `score`: number
    - `content_snippet`: string

### 2.3 Upsert Vectors (L4, 8 KBs)

**UpsertVectors**

- **Request:**
  - `kb_name`: KnowledgeBaseName
  - `points`: Array of:
    - `id`: string
    - `vector`: number[]
    - `payload`: Record<string, string>
- **Response:**
  - `success`: boolean
  - `upserted_count`: number

### 2.4 Execute Action (Skills)

**ExecuteAction**

- **Request:**
  - `skill_name`: string
  - `params`: Record<string, string>
  - `depth`: number
  - `reasoning_id`: string (UUID)
  - `mock_mode`?: boolean
  - `timeout_ms`?: number
- **Response:**
  - `observation`: string
  - `success`: boolean
  - `error`: string (non-empty on failure)

### 2.5 RLM (Single Step)

**DelegateRLM / POST /rlm**

- **Request:**
  - `query`: string
  - `context`?: string
  - `depth`?: number (0..max_depth)
- **Response:**
  - `summary`: string
  - `converged`: boolean

---

## 3. WebSocket Agent Events (Real-Time Reasoning)

Every WebSocket message is an object with at least:

- `event`: string (event type)
- `timestamp`: string (ISO 8601)
- `reasoning_id`?: string (UUID, when in a reasoning run)
- Plus event-specific payload below.

### 3.1 Event Kind Union

```ts
type AgentEventKind =
  | "session_started"
  | "thought"
  | "action_planned"
  | "action_started"
  | "action_completed"
  | "memory_read"
  | "memory_written"
  | "search_issued"
  | "search_result"
  | "converged"
  | "error"
  | "session_ended";
```

### 3.2 Per-Event Payload (Contract)

| Event | Additional payload |
|-------|--------------------|
| `session_started` | `session_id`: string, `query`: string, `depth`: number |
| `thought` | `thought`: string, `depth`: number |
| `action_planned` | `skill_name`: string, `params`: Record<string, string>, `depth`: number |
| `action_started` | `skill_name`: string |
| `action_completed` | `skill_name`: string, `success`: boolean, `observation`: string, `error`?: string |
| `memory_read` | `layer`: number, `key`: string, `data`?: string |
| `memory_written` | `layer`: number, `key`: string |
| `search_issued` | `kb_name`: string, `query`: string, `limit`: number |
| `search_result` | `kb_name`: string, `hits_count`: number, `top_snippet`?: string |
| `converged` | `summary`: string, `final_summary`?: string |
| `error` | `message`: string, `component`?: string |
| `session_ended` | `session_id`: string, `converged`: boolean, `summary`?: string |

### 3.3 Base Agent Event (TypeScript Shape)

```ts
interface AgentEventBase {
  event: AgentEventKind;
  timestamp: string; // ISO 8601
  reasoning_id?: string;
}
// Each event = AgentEventBase & { event: "thought"; thought: string; depth: number } etc.
```

---

## 4. Memory Layer Constants

| Layer | Name | Implemented |
|-------|------|-------------|
| 1 | Sensory (L1) | Yes |
| 2 | Working (L2) | Yes |
| 3 | Episodic | No (stub) |
| 4 | Semantic (8 KBs) | Yes |
| 5 | Procedural | Stub |
| 6 | Conceptual | Stub |
| 7 | Identity | Stub |

Valid **short-term** layers for API: `1`, `2`.  
Valid **semantic** operations: use `SemanticSearch` / `UpsertVectors` with one of the 8 `kb_name` values.

---

## 5. Version and Compatibility

- **Contract version:** 1.0.0
- **Proto:** `pagi.proto` (current); regenerate stubs after proto changes.
- **Sync:** When adding or changing a field, update (1) this doc, (2) `contract/types.ts`, (3) `mock_provider.py`.
