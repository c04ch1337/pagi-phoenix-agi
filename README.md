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

## Configuration

Copy `.env.example` to `.env` and customize. Load before run: `source .env` (Unix) or set vars manually (Windows), or use `make run` (Makefile runs `load-env` and sources `.env` when present). Ports, depth cap, Qdrant URI, and self-evolution paths are configurable; see `.env.example` for all keys.

## Quick Start

```bash
make build
make test
# Phase 2: L4 semantic requires Qdrant on PAGI_QDRANT_URI (default localhost:6334) — see make qdrant
make run
```

### Validate Proto (Python)

From `pagi-intelligence-bridge` with Poetry installed:

```bash
poetry install
poetry run python scripts/peek_proto.py
```

This compiles `pagi.proto` to Python stubs under `src/pagi_pb/` and prints generated message names.

## Testing Self-Healing

- Run the bridge: `make run-python` (ensure `PAGI_SELF_HEAL_LOG` is set in `.env`, e.g. to `agent_actions.log`).
- Test Python heal flow: `make test-self-heal`
  - Asserts log entry in `PAGI_SELF_HEAL_LOG` (default `agent_actions.log`).
  - Optional grpcurl: Simulates ProposePatch; install via `cargo install grpcurl` or brew/apt.
- Expected: Curl triggers error → `_report_self_heal` appends to log → grep succeeds; gRPC returns stub PatchResponse if orchestrator is running.

- Test Rust heal: `make test-rust-heal`
  - Requires orchestrator running (`make run` or run core-orchestrator separately). Triggers `SimulateError` → propose/apply cycle with HITL denial; orchestrator appends "Heal cycle simulated" to `PAGI_SELF_HEAL_LOG`.
  - Asserts that log entry; use Git Bash on Windows for grep/sleep.
- Force test-failure path: `make test-fail-sim` (or `PAGI_FORCE_TEST_FAIL=true make test-rust-heal`)
  - With `PAGI_FORCE_TEST_FAIL=true`, `apply_patch` skips real tests and returns an internal error; `SimulateError` passes HITL so this path is exercised, still logs and returns Ok for assertion.

## Traceability

Design decisions and setup proposals are logged for L6 recursive memory and future evolutions.
