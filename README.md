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

## Blueprint Status: Complete

- **Pillars:** Rust backbone (orchestrator, Watchdog, SafetyGovernor, MemoryManager), Python intelligence (RLM, skills, LiteLLM), pagi-proto (gRPC contracts), L5 skills registry, self-evolution (ProposePatch/ApplyPatch, HITL, auto-evolve), verticals (research, codegen, code_review).
- **Gaps closed:** Bridge self-heal wired to orchestrator via gRPC (`PAGI_ALLOW_SELF_HEAL_GRPC`); error → ProposePatch → optional ApplyPatch (when `requires_hitl=false`) with log observability. No schema drift; HITL preserved for required patches.
- **Use as template:** Copy repo (e.g. via `scripts/init-new-app.sh` or clone), override verticals and L5 skills per app; see `TEMPLATE_README.md`.

## Configuration

Copy `.env.example` to `.env` and customize. Load before run: `source .env` (Unix) or set vars manually (Windows), or use `make run` (Makefile runs `load-env` and sources `.env` when present). Ports, depth cap, Qdrant URI, and self-evolution paths are configurable; see `.env.example` for all keys.

## Quick Start

```bash
make build
make test
# Phase 2: L4 semantic requires Qdrant on PAGI_QDRANT_URI (default localhost:6334) — see make qdrant
make run
```

## Frontend Integration

Copy your UI (e.g. from **Google AI Studio**) into `pagi-frontend/src/`, then run `make run-frontend` (or `cd pagi-frontend && npm start`). Point the frontend at the bridge and orchestrator: **HTTP** via `PAGI_HTTP_PORT` (default 8000) for `/rlm`, `/rlm-multi-turn`, `/health`; **gRPC** via `PAGI_GRPC_ADDR` (default `[::1]:50051`) for memory, actions, and self-heal. Set `PAGI_FRONTEND_PORT` in `.env` if your app uses a different dev port (e.g. 3000).

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
- When **PAGI_ALLOW_SELF_HEAL_GRPC=true**, bridge errors trigger ProposePatch/ApplyPatch via gRPC (optional auto-apply when `requires_hitl=false`).
- **Verify wiring:** `make verify-self-heal-grpc`

- Test Rust heal: `make test-rust-heal`
  - Requires orchestrator running (`make run` or run core-orchestrator separately). Triggers `SimulateError` → propose → poll for `PAGI_APPROVE_FLAG` (up to `PAGI_HITL_POLL_SECS`) → apply when flag present or after timeout (HITL denial); orchestrator appends "Heal cycle simulated" to `PAGI_SELF_HEAL_LOG`.
  - Asserts that log entry; use Git Bash on Windows for grep/sleep. To test apply with HITL: create `approve.patch` (or `PAGI_APPROVE_FLAG`) in the core dir before the poll window ends.
- Force test-failure path: `make test-fail-sim` (or `PAGI_FORCE_TEST_FAIL=true make test-rust-heal`)
  - With `PAGI_FORCE_TEST_FAIL=true`, `apply_patch` skips real tests and returns an internal error; `SimulateError` passes HITL so this path is exercised, still logs and returns Ok for assertion.

## Verifying L5 chaining (peek → execute → save)

With local dispatch enabled, the RLM can chain allow-listed skills via `execute_skill`. Run the steps below to observe the full Think → Act → Observe loop without Rust.

**1. Start the bridge**

```bash
cd pagi-intelligence-bridge
poetry run uvicorn src.main:app --reload --port 8000
```

Verify env is loaded: `curl http://127.0.0.1:8000/health/env` — you should see `PAGI_ALLOW_LOCAL_DISPATCH`, `PAGI_MOCK_MODE`, etc. If vars are missing, put them in a `.env` in `pagi-intelligence-bridge/` or set them in the same shell before starting uvicorn.

**2. Set environment in the bridge process** (same terminal as step 1, or in `.env` if your runner loads it). The server must see these; setting them only in the terminal where you run `curl` is not enough.

```bash
export PAGI_ALLOW_LOCAL_DISPATCH=true
export PAGI_MOCK_MODE=true
export PAGI_ACTIONS_VIA_GRPC=false
export PAGI_AGENT_ACTIONS_LOG=agent_actions.log
```

- **Reproducible chain without an LLM:** set `PAGI_MOCK_MODE=false` and set `PAGI_RLM_STUB_JSON` to a JSON object with `thought`, `action` (e.g. `execute_skill` with `peek_file` in params), and `is_final`. The bridge will then run the think/act/observe path and log EXECUTING + observations.
- **With a real model:** keep `PAGI_MOCK_MODE=false` and, if the model doesn't chain naturally, use `PAGI_RLM_STUB_JSON` as above to force a structured step.

**3. Trigger the chain** (from another terminal; bridge on port 8000)

Basic chain (peek → execute_skill → save):

```bash
curl -X POST http://127.0.0.1:8000/rlm \
  -H "Content-Type: application/json" \
  -d '{
    "query": "First peek the beginning of README.md, then use execute_skill to save a new file called test_chained.py that prints the peeked content",
    "context": "",
    "depth": 0
  }'
```

Discovery chain (list_dir → peek → save); use with a real model or a stub that calls `list_dir` then follows up:

```bash
curl -X POST http://127.0.0.1:8000/rlm \
  -H "Content-Type: application/json" \
  -d '{
    "query": "List files in current directory, peek the first README.md you find, then save a new file called discovered.py that prints a summary of what you saw",
    "context": "",
    "depth": 0
  }'
```

Expected discovery chain: `list_dir` → `peek_file` (on discovered path) → `save_skill` (discovered.py). To drive the first step with a stub (no LLM), set `PAGI_RLM_STUB_JSON` in the **same shell** as the bridge to a JSON with `"skill_name":"list_dir"` and `"params":{"path":".","pattern":"*.md","max_items":5}`; run uvicorn **without** `--reload` so the process inherits env, or use `make verify-l5-chain` (Unix) which passes stub env inline. The full chain in one shot (list_dir → peek → save) requires a real model or multiple requests with context from the previous step.

Full-file read with size cap (`read_entire_file_safe`):

```bash
curl -X POST http://127.0.0.1:8000/rlm \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Read the entire content of pagi.proto and summarize it",
    "context": "",
    "depth": 0
  }'
```

Use a stub with `"skill_name":"read_entire_file_safe"` and `"params":{"path":"pagi-proto/pagi.proto","max_size_bytes":1048576}` (or set path relative to bridge cwd). Observation will contain the file content (capped); summary is the thought unless the loop is extended to include it.

Read entire file and save summary (e.g. `read_entire_file_safe` then `save_skill`):

```bash
curl -X POST http://127.0.0.1:8000/rlm \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Read the entire pagi.proto file and save a summary as proto_summary.py",
    "context": "",
    "depth": 0
  }'
```

With a real model or a stub that chains `read_entire_file_safe` (path `pagi-proto/pagi.proto`) and `save_skill` (filename `proto_summary.py`), the observation from the read is used as context for the save step.

Chain list_dir → read_entire_file_safe → write_file_safe (list md files, read first fully, write to backup):

```bash
curl -X POST http://127.0.0.1:8000/rlm \
  -H "Content-Type: application/json" \
  -d '{
    "query": "List md files, read the first one fully, write its content to a backup file",
    "context": "",
    "depth": 0
  }'
```

With a real model or stubs that chain `list_dir` (e.g. `pattern":"*.md"`), then `read_entire_file_safe` on the chosen path, then `write_file_safe` (path e.g. `backup/README_backup.md`, content from observation, `overwrite: true` if needed), the loop performs discovery → full read → safe write. Ensure `PAGI_PROJECT_ROOT` includes the directory you write into.

Recursive discovery with `list_files_recursive` (e.g. list `.py` in skills dir, read first fully, write summary to `skills_summary.py`):

```bash
curl -X POST http://127.0.0.1:8000/rlm \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Recursively list py files in the skills directory, read the first one fully, then write a short summary to skills_summary.py",
    "context": "",
    "depth": 0
  }'
```

Use a stub or real model that chains `list_files_recursive` (e.g. `path": "src/skills"`, `pattern": "*.py"`, `max_depth": 2`, `max_items`: 50), then `read_entire_file_safe` on the first path from the listing, then `write_file_safe` for `skills_summary.py`.

Search codebase for patterns (`search_codebase`; e.g. keyword "panic" or regex):

```bash
curl -X POST http://127.0.0.1:8000/rlm \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Search codebase for panic keywords in the project",
    "context": "",
    "depth": 0
  }'
```

Use a stub with `"skill_name":"search_codebase"` and `"params":{"path":".","pattern":"panic","max_files":50,"mode":"keyword"}` to get file:line matches (or use `"mode":"regex"` for regex search).

Run tests in sandbox (`run_tests`; Python pytest or Rust cargo test):

```bash
curl -X POST http://127.0.0.1:8000/rlm \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Run Python tests in the bridge directory",
    "context": "",
    "depth": 0
  }'
```

Use a stub with `"skill_name":"run_tests"` and `"params":{"dir":"pagi-intelligence-bridge","type":"python","timeout_sec":30}` (or `"type":"rust"` and `"dir":"pagi-core-orchestrator"` for Rust tests).

Run a Python code snippet in sandbox (`run_python_code_safe`; restricted globals, timeout, captured stdout):

```bash
curl -X POST http://127.0.0.1:8000/rlm \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Run this Python code snippet",
    "context": "code: print(2 + 2)",
    "depth": 0
  }'
```

Use a stub with `"skill_name":"run_python_code_safe"` and `"params":{"code":"print(2 + 2)","timeout_sec":5,"max_output_len":4096}` to get the snippet output (e.g. `4`) or a prefixed error.

Analyze code snippet for errors (RCA primitive with `analyze_code`):

```bash
curl -X POST http://127.0.0.1:8000/rlm \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Analyze this code snippet for errors and propose fix",
    "context": "code: fn main() { panic!(\"oops\"); }",
    "depth": 0
  }'
```

Use a stub with `"skill_name":"analyze_code"` and `"params":{"code":"fn main() { panic!(\"oops\"); }","language":"rust","max_length":4096}` to get an RCA summary (e.g. panic! detected).

**4. What to look for**

- **Terminal:** THOUGHT → EXECUTING `execute_skill` (or `list_dir`) → EXECUTING `peek_file` → observation from peek → observation from save → final summary.
- **agent_actions.log:** ACTION lines with `reasoning_id` and observations.
- **Response body:** `RLMSummary` with `converged=true` and a synthesis that includes the chain result.

Once you see chained observations logged and returned, the local L5 chaining loop is verified. **Automated run:** from the project root, `make verify-l5-chain` (requires poetry, curl; optional jq) starts the bridge with stub env, triggers the chain, and greps `agent_actions.log` for EXECUTING/THOUGHT/OBSERVATION. If env is not visible to the bridge (e.g. reloader child on some setups), use `make verify-l5-chain-no-reload` instead — it runs uvicorn without `--reload` so the single process inherits env and the list_dir stub runs. Next options: add more primitive skills or wire real Rust-mediated dispatch (sandbox, timeout, allow-list from registry).

### Multi-turn RLM session

Use the previous response’s `context` (or summary) as the next request’s `context` to chain reasoning across turns. Example: query1 → `list_dir`, query2 → `read_entire_file_safe` on the first path from context, query3 → `write_file_safe` with a summary. From the project root, `make verify-multi-turn` runs a 3-step chain (list_dir → read_entire_file_safe → write_file_safe) and asserts `converged=true` and three ACTION/EXECUTING lines in the log.

**Single-call multi-turn endpoint** (`POST /rlm-multi-turn`): run a context-chained session in one request. The bridge calls `recursive_loop` repeatedly, injecting each summary into the next turn's context until `converged` or `max_turns` is reached. Response is a list of `RLMSummary` dicts. Example for self-patch workflow (analyze → propose → test → apply, up to 4 turns):

```bash
curl -X POST http://127.0.0.1:8000/rlm-multi-turn \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Analyze error, propose fix, test, apply",
    "context": "error_trace: panic at main.rs:42",
    "depth": 0,
    "max_turns": 4
  }'
```

Context is capped by `PAGI_MULTI_TURN_CONTEXT_MAX_CHARS` (default 10000) to avoid unbounded growth.

### Complete local loop

The system supports **discovery → read → write** chaining locally (Python allow-list) or via Rust-mediated dispatch (allow-list, timeout, no shell, logging). L5 registry includes `list_dir`, `list_files_recursive`, `read_entire_file_safe`, `write_file_safe`, `peek_file`, `save_skill`, `execute_skill`. No schema changes required for multi-turn; optional `PAGI_MULTI_TURN_CONTEXT_MAX_TOKENS` caps accumulated context.

### Vertical use-case: self-patch codegen

With `PAGI_VERTICAL_USE_CASE=research`, the RLM prioritizes self-patch for errors: RCA → propose code → save to L5. Use with local or gRPC dispatch so `write_file_safe` can persist proposed fixes. When **PAGI_AUTO_EVOLVE_SKILLS=true**, a successful Python patch apply (in the Watchdog) triggers auto-evolve of a new L5 skill from the patch content: the orchestrator calls `evolve_skill_from_patch`, then commits the new skill in the bridge repo with message "Auto-evolved skill from self-patch". Example (bridge running with `PAGI_ALLOW_LOCAL_DISPATCH=true` and `PAGI_VERTICAL_USE_CASE=research`):

```bash
curl -X POST http://127.0.0.1:8000/rlm \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Analyze error_trace, propose Rust fix, save to patch.rs",
    "context": "error_trace: panic at main.rs:42",
    "depth": 0
  }'
```

With a real model or a stub that returns a thought containing the proposed fix and `is_final: true`, the vertical hook can write the fix to `PAGI_SELF_PATCH_DIR`/patch_rs.txt (default `patches/` under `PAGI_PROJECT_ROOT`). HITL remains required for Rust core patches: the orchestrator polls for `PAGI_APPROVE_FLAG` (e.g. `approve.patch`) in the core dir for up to `PAGI_HITL_POLL_SECS` after propose (SimulateError or real heal), then apply when the file is present. When `PAGI_AUTO_COMMIT_SELF_PATCH=true`, successful apply auto-commits the patch file to the registry Git (evolution traceability). When `PAGI_AUTO_EVOLVE_SKILLS=true`, a successful `python_skill` apply (and auto-commit) triggers auto-evolution: the orchestrator calls the bridge skill `evolve_skill_from_patch` with the patch content, then parses the returned `EVOLVED_PATH`, adds and commits that file in the bridge Git repo with commit message "Auto-evolved skill from self-patch".

### Vertical: AI codegen

With `PAGI_VERTICAL_USE_CASE=codegen`, the RLM biases toward generating code (snippets, tests, refactors). On convergence (`is_final: true`), the bridge forces a `write_file_safe` to `PAGI_CODEGEN_OUTPUT_DIR`/`<timestamp>.py` (default `codegen_output/` under `PAGI_PROJECT_ROOT`) with the thought content as generated code. Requires `PAGI_ALLOW_LOCAL_DISPATCH=true` or gRPC dispatch. Example (bridge with codegen vertical and local dispatch):

```bash
curl -X POST http://127.0.0.1:8000/rlm \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Generate a test for the analyze_code skill",
    "context": "",
    "depth": 0
  }'
```

Use a real model or `PAGI_RLM_STUB_JSON` with `is_final: true` and a `thought` containing code; the response summary will include "Codegen write" and the path under `codegen_output/`.

### Vertical: AI Code Review Agent

With `PAGI_VERTICAL_USE_CASE=code_review`, the RLM prioritizes code review: analyze for issues, propose fixes, run tests, save reviewed code. On convergence (`is_final: true`), the bridge forces a chain: **analyze_code** (on code from context or thought) → **run_tests** (Python in `PAGI_PROJECT_ROOT`) → **write_file_safe** to `PAGI_CODE_REVIEW_OUTPUT_DIR`/`reviewed_<timestamp>.py` (default `reviewed/` under `PAGI_PROJECT_ROOT`). Requires `PAGI_ALLOW_LOCAL_DISPATCH=true` or gRPC dispatch. Example (bridge with code_review vertical and local dispatch):

```bash
curl -X POST http://127.0.0.1:8000/rlm \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Review this code for issues and propose fixes",
    "context": "code: def add(a, b): return a + b",
    "depth": 0
  }'
```

Use a real model or a stub with `is_final: true` and a `thought` containing the proposed fix; the response summary will include "Code review", run_tests output, and the write_file_safe observation; the reviewed file is written under `reviewed/`.

### Verify Rust dispatch

Rust-mediated L5 execution is gated by `PAGI_ALLOW_REAL_DISPATCH` and uses an allow-list, timeout, and no-shell subprocess. From the project root:

- **Rust tests only:** `make test-rust` (or `cd pagi-core-orchestrator && cargo test`) — runs dispatch tests: mock observation when `PAGI_MOCK_MODE=true` or real disabled, unknown skill returns "Skill not in registry", timeout returns "Execution timed out".
- **Full dispatch verification:** `make verify-rust-dispatch` — starts orchestrator with `PAGI_ALLOW_REAL_DISPATCH=true` and bridge with `PAGI_ACTIONS_VIA_GRPC=true`, triggers `/rlm`, and asserts an ACTION line with `reasoning_id` in `agent_actions.log`. Expect mediated observation and log lines.

## Traceability

Design decisions and setup proposals are logged for L6 recursive memory and future evolutions.
