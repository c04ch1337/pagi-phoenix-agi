# Phoenix AGI (pagi)

High-level blueprint for a recursive AGI system with tiered memory, self-healing, and bare-metal sovereignty.

## Architecture

- **pagi-core-orchestrator** (Rust): Service backbone, 7-layer memory manager, watchdog with future SafetyGovernor (Blue Team wrapping on outbound calls).
- **pagi-intelligence-bridge** (Python): MIT RLM (recursive loop), Pydantic-typed data models, local embeddings (Sentence Transformers, 1536-dim cap), OpenRouter via LiteLLM.
- **pagi-proto**: Shared gRPC contracts (memory access, RLM delegation, self-heal signals).
- **pagi-skills**: Evolution registry; Git submodules for atomic rollbacks.

## Constraints

- No Docker; Rust/Cargo and Python 3.10+ on bare metal.
- Recursion depth circuit breaker at 5 levels; delegate to summarized JSON tree (L6) when exceeded.
- L4 semantic memory: Qdrant, local embeddings; cap 1536 dimensions for RAM balance.

## Quick Start

```bash
make build
make test
make run
```

### Validate Proto (Python)

From `pagi-intelligence-bridge` with Poetry installed:

```bash
poetry install
poetry run python scripts/peek_proto.py
```

This compiles `pagi.proto` to Python stubs under `src/pagi_pb/` and prints generated message names.

## Traceability

Design decisions and setup proposals are logged for L6 recursive memory and future evolutions.
