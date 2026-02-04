# Phoenix AGI Microservice Hierarchy

## Pillars

1. **Rust Backbone (pagi-core-orchestrator)**  
   Orchestration, MemoryManager (7-layer hierarchy), SafetyGovernor, Watchdog.  
   - **Entry:** `main.rs`  
   - **Build:** `cargo build`  
   - **gRPC:** `[::1]:PAGI_GRPC_PORT` (default 50051)

2. **Python Intelligence (pagi-intelligence-bridge)**  
   RLM REPL (`recursive_loop.py`), skills registry (`src/skills`).  
   - **Entry:** `main.py` (FastAPI)  
   - **Run:** `poetry run uvicorn src.main:app --port PAGI_HTTP_PORT`  
   - **HTTP:** `127.0.0.1:PAGI_HTTP_PORT` (default 8000)

3. **Shared contracts (pagi-proto)**  
   `pagi.proto` defines gRPC services and messages. Regenerate stubs after changes (Rust: `cargo build`; Python: `scripts/peek_proto.py`).

4. **Evolution Registry (pagi-skills)**  
   Git-backed store for patches and L5 procedural traceability; Watchdog commits from core-orchestrator.

---

## Hierarchy enforcement

- **Memory / I/O:** All persistent memory and file I/O for system state go through the Rust MemoryManager (gRPC). Python does not perform direct disk access for memory or registry persistence outside the local skills dir used by the L5 stub.
- **Safety:** Outbound calls (e.g. OpenRouter) are intended to be routed via Rust SafetyGovernor (gRPC) for depth and HITL checks.
- **Self-heal:** Errors in the bridge can be reported to the Watchdog (ProposePatch/ApplyPatch) for RCA and patch proposals.

---

## Troubleshooting flow

| Step           | Command / action |
|----------------|-------------------|
| **Compilation**| `make check-proto` (validates proto); `make build` |
| **Incremental**| `make build-incremental` (requires `cargo-watch`, `watchmedo`) |
| **Health**     | `make health-check` (Python `/health`, Rust gRPC `pagi.Pagi`, Qdrant `PAGI_QDRANT_URI/healthz`) |
| **L4 bootstrap** | `make index-kb` (index ARCHITECTURE.md + README.md into kb_core; requires Qdrant + orchestrator) |
| **Logs**       | `tail agent_actions.log`; Rust: `PAGI_LOG_LEVEL` (or `RUST_LOG`) controls env_logger |
| **Debug self-heal** | `make debug-self-heal` → then inspect `agent_actions.log` (when `PAGI_SELF_HEAL_LOG` set) |

---

## Diagram (text)

```
                    ┌─────────────────────────────────────┐
                    │         pagi.proto (gRPC)           │
                    └─────────────────────────────────────┘
                                      │
         ┌────────────────────────────┼────────────────────────────┐
         ▼                                                            ▼
┌─────────────────────┐                                    ┌─────────────────────┐
│ Rust Backbone       │                                    │ Python Intelligence │
│ (core-orchestrator) │◄──── AccessMemory, DelegateRLM, ───►│ (intelligence-bridge)│
│ MemoryManager       │     SemanticSearch, UpsertVectors,  │ recursive_loop      │
│ SafetyGovernor      │     ProposePatch, ApplyPatch,      │ embed_and_upsert    │
│ Watchdog            │     SelfHeal                       │ skills/ FastAPI :8000│
└─────────────────────┘                                    └─────────────────────┘
         │                                                            │
         └──────────────────────┬────────────────────────────────────┘
                                 ▼
                    ┌─────────────────────────────────────┐
                    │ Evolution Registry (pagi-skills)     │
                    │ Git-Watcher commits from Rust        │
                    └─────────────────────────────────────┘
```
