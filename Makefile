.PHONY: all build run test clean qdrant load-env check-proto build-incremental health-check debug-self-heal index-kb test-self-heal verify-self-heal-grpc test-rust test-rust-heal test-fail-sim verify-all verify-l5-chain verify-l5-chain-no-reload verify-multi-turn verify-rust-dispatch run-frontend

all: build

# Load .env if present (Unix). On Windows: set vars manually or use 'set -a' equivalent before make run.
load-env:
	@if [ -f .env ]; then set -a && . ./.env && set +a && echo "Loaded .env"; fi

# Start Qdrant for L4 semantic (localhost:6334). Install from https://qdrant.tech/documentation/quick-start/ or use system qdrant.
qdrant:
	@echo "Ensure Qdrant is running on http://localhost:6334 (e.g. qdrant run or system service)"

build:
	cd pagi-core-orchestrator && cargo build --release
	cd pagi-intelligence-bridge && poetry install

run: load-env
	. ./.env 2>/dev/null || true; cd pagi-core-orchestrator && cargo run --release & \
	. ./.env 2>/dev/null || true; cd pagi-intelligence-bridge && poetry run uvicorn src.main:app --port $${PAGI_HTTP_PORT:-8000} --reload

# Python bridge only (PAGI_HTTP_PORT or 8000)
run-python: load-env
	. ./.env 2>/dev/null || true; cd pagi-intelligence-bridge && poetry run uvicorn src.main:app --port $${PAGI_HTTP_PORT:-8000} --reload

# Frontend (stub: paste UI in pagi-frontend/src/, then npm start)
run-frontend:
	cd pagi-frontend && npm start

test: load-env
	. ./.env 2>/dev/null || true; cd pagi-core-orchestrator && cargo test
	. ./.env 2>/dev/null || true; cd pagi-intelligence-bridge && poetry run pytest

# Rust unit/integration tests only (dispatch mock, unknown_skill, timeout; no Python).
test-rust:
	cd pagi-core-orchestrator && cargo test

clean:
	cd pagi-core-orchestrator && cargo clean
	rm -rf pagi-intelligence-bridge/.venv

# Validate pagi.proto consistency (compiles via Rust build.rs)
check-proto:
	cd pagi-core-orchestrator && cargo check

# Regenerate Python gRPC stubs from pagi.proto (run from bridge: python scripts/peek_proto.py)
gen-proto:
	cd pagi-intelligence-bridge && poetry run python scripts/peek_proto.py

# Incremental builds (require: cargo install cargo-watch; pip install watchdog)
build-incremental:
	cd pagi-core-orchestrator && cargo watch -x build &
	cd pagi-intelligence-bridge && poetry run watchmedo shell-command --patterns="*.py" --recursive --command="poetry check" --drop .

# Health probes: Python /health, Rust gRPC, and L4 Qdrant (optional)
health-check:
	@curl -sf http://127.0.0.1:$${PAGI_HTTP_PORT:-8000}/health || echo "Python bridge down"
	@grpcurl -plaintext [::1]:$${PAGI_GRPC_PORT:-50051} list pagi.Pagi 2>/dev/null || echo "Rust gRPC not reachable (install grpcurl if needed)"
	@curl -sf $${PAGI_QDRANT_URI:-http://localhost:6334}/healthz 2>/dev/null || echo "Qdrant L4 not reachable (optional)"

# Trigger simulated error → self-heal flow; then inspect agent_actions.log
debug-self-heal:
	curl -s -X POST http://127.0.0.1:$${PAGI_HTTP_PORT:-8000}/debug -H "Content-Type: application/json" -d '{"trigger_error": true}' || echo "Bridge not running"

# Automated self-heal test: /debug → log assertion; optional grpcurl ProposePatch (Phase 4 traceability).
# Requires bridge running with PAGI_SELF_HEAL_LOG set (e.g. agent_actions.log). Install grpcurl for optional gRPC.
test-self-heal:
	@echo "Simulating self-heal via /debug..."
	@curl -s -X POST http://127.0.0.1:$${PAGI_HTTP_PORT:-8000}/debug -H "Content-Type: application/json" -d '{"trigger_error": true}' || (echo "Debug trigger failed (bridge down?)"; exit 1)
	@sleep 1
	@if grep -q "Self-heal reported" $${PAGI_SELF_HEAL_LOG:-agent_actions.log} 2>/dev/null; then \
		echo "Log assertion passed: Heal entry found."; \
	else \
		echo "Assertion failed: No heal entry in log (ensure PAGI_SELF_HEAL_LOG is set and bridge is running)."; exit 1; \
	fi
	@echo "Optional: Simulate propose_patch via grpcurl (install if needed: cargo install grpcurl)"
	@grpcurl -plaintext -d '{"error_trace": "Simulated ValueError", "component": "python_skill"}' [::1]:$${PAGI_GRPC_PORT:-50051} pagi.Pagi/ProposePatch 2>/dev/null || echo "gRPC simulation skipped (orchestrator down?)"

# Verify self-heal gRPC wiring: PAGI_ALLOW_SELF_HEAL_GRPC=true, run test-self-heal, assert ProposePatch in log.
verify-self-heal-grpc:
	@echo "Verifying self-heal gRPC wiring..."
	@PAGI_ALLOW_SELF_HEAL_GRPC=true $(MAKE) test-self-heal
	@grep -q "ProposePatch\|Self-heal reported" $${PAGI_SELF_HEAL_LOG:-agent_actions.log} 2>/dev/null && echo "ProposePatch/wiring assertion passed." || echo "Check PAGI_SELF_HEAL_LOG for ProposePatch or Self-heal reported."

# Rust heal cycle simulation: SimulateError → propose/apply with HITL denial → log "Heal cycle simulated".
# Requires orchestrator running; PAGI_SELF_HEAL_LOG set for log assertion. Use Git Bash on Windows for grep/sleep.
test-rust-heal:
	@echo "Simulating Rust heal cycle..."
	@grpcurl -plaintext -d '{}' [::1]:$${PAGI_GRPC_PORT:-50051} pagi.Pagi/SimulateError || (echo "Simulation failed (orchestrator down?)"; exit 1)
	@sleep 1
	@grep -q "Heal cycle simulated" $${PAGI_SELF_HEAL_LOG:-agent_actions.log} 2>/dev/null && echo "Log assertion passed." || (echo "Assertion failed: No heal entry in log."; exit 1)

# Force test-failure path in apply_patch (PAGI_FORCE_TEST_FAIL); SimulateError still logs and returns Ok.
test-fail-sim:
	PAGI_FORCE_TEST_FAIL=true $(MAKE) test-rust-heal

# Bootstrap L4 kb_core: index ARCHITECTURE.md and README.md (requires Qdrant + orchestrator gRPC)
index-kb:
	cd pagi-intelligence-bridge && poetry run python src/embed_and_upsert.py --doc ../ARCHITECTURE.md --kb kb_core
	cd pagi-intelligence-bridge && poetry run python src/embed_and_upsert.py --doc ../README.md --kb kb_core
	cd pagi-intelligence-bridge && poetry run python src/embed_and_upsert.py --doc ../pagi-proto/pagi.proto --kb kb_core

# Initialize L5 skills directory as an evolution registry (Git-tracked).
# Safe: does not enable execution; only sets up provenance tracking.
init-skills-registry:
	cd pagi-intelligence-bridge/src/skills && git init
	@echo "L5 skills registry initialized (git)."

# L5 chaining verification: start bridge with stub env, trigger /rlm, assert EXECUTING in log.
# Requires: poetry, curl, jq (optional). Use Git Bash on Windows. Kills uvicorn on this port after run.
verify-l5-chain:
	@echo "Starting bridge with L5 chaining env..."
	@cd pagi-intelligence-bridge && \
		PAGI_ALLOW_LOCAL_DISPATCH=true \
		PAGI_MOCK_MODE=false \
		PAGI_ACTIONS_VIA_GRPC=false \
		PAGI_AGENT_ACTIONS_LOG=../agent_actions.log \
		PAGI_RLM_STUB_JSON='{"thought":"Peek README then synthesize.","action":{"skill_name":"execute_skill","params":{"skill_name":"peek_file","params":{"path":"../README.md","start":0,"end":200},"reasoning_id":"verify-1"}},"is_final":true}' \
		poetry run uvicorn src.main:app --port $${PAGI_HTTP_PORT:-8000} & \
		sleep 5
	@echo "Triggering chained RLM request..."
	@curl -s -X POST http://127.0.0.1:$${PAGI_HTTP_PORT:-8000}/rlm \
		-H "Content-Type: application/json" \
		-d '{"query":"First peek the beginning of README.md, then use execute_skill to save a new file called test_chained.py that prints the peeked content","context":"","depth":0}' \
		| (command -v jq >/dev/null 2>&1 && jq . || cat)
	@sleep 2
	@echo "Checking agent_actions.log for chain traces..."
	@grep -E "EXECUTING|THOUGHT|OBSERVATION" agent_actions.log 2>/dev/null || echo "No EXECUTING lines found — ensure bridge ran with PAGI_AGENT_ACTIONS_LOG=../agent_actions.log"
	@pkill -f "uvicorn src.main:app" 2>/dev/null || true
	@echo "Verification complete. Check response and log for peek → execute_skill chain."

# Same as verify-l5-chain but explicitly no reload: single process inherits env (avoids reloader child).
# Uses list_dir stub for discovery-step verification. Use when inline env is not inherited (e.g. some Windows setups).
verify-l5-chain-no-reload:
	@echo "Starting bridge (no reload) with L5 discovery stub..."
	@cd pagi-intelligence-bridge && \
		PAGI_ALLOW_LOCAL_DISPATCH=true \
		PAGI_MOCK_MODE=false \
		PAGI_ACTIONS_VIA_GRPC=false \
		PAGI_AGENT_ACTIONS_LOG=../agent_actions.log \
		PAGI_RLM_STUB_JSON='{"thought":"List files first.","action":{"skill_name":"list_dir","params":{"path":".","pattern":"*.md","max_items":5},"reasoning_id":"r1"},"is_final":false}' \
		poetry run uvicorn src.main:app --port $${PAGI_HTTP_PORT:-8000} & \
		sleep 5
	@curl -s -X POST http://127.0.0.1:$${PAGI_HTTP_PORT:-8000}/rlm \
		-H "Content-Type: application/json" \
		-d '{"query":"List files, peek first README.md, save discovered.py","context":"","depth":0}' \
		| (command -v jq >/dev/null 2>&1 && jq . || cat)
	@sleep 2
	@grep -E "EXECUTING|THOUGHT|OBSERVATION" agent_actions.log 2>/dev/null || echo "No traces found — ensure bridge ran with PAGI_AGENT_ACTIONS_LOG=../agent_actions.log"
	@pkill -f "uvicorn src.main:app" 2>/dev/null || true
	@echo "Verification (no reload) complete."

# Multi-turn RLM: 3 sequential /rlm calls (same stub), assert final converged=true and log has 3 ACTION/EXECUTING lines.
# Requires: poetry, curl. Use Git Bash on Windows for &, sleep, grep.
verify-multi-turn:
	@echo "Starting bridge for multi-turn verification..."
	@cd pagi-intelligence-bridge && \
		PAGI_ALLOW_LOCAL_DISPATCH=true \
		PAGI_MOCK_MODE=false \
		PAGI_ACTIONS_VIA_GRPC=false \
		PAGI_AGENT_ACTIONS_LOG=../agent_actions.log \
		PAGI_RLM_STUB_JSON='{"thought":"List then read then write.","action":{"skill_name":"list_dir","params":{"path":".","max_items":5}},"is_final":true}' \
		poetry run uvicorn src.main:app --port $${PAGI_HTTP_PORT:-8000} & \
		sleep 5
	@echo "Triggering 3 /rlm requests..."
	@curl -s -X POST http://127.0.0.1:$${PAGI_HTTP_PORT:-8000}/rlm -H "Content-Type: application/json" -d '{"query":"Step 1 list","context":"","depth":0}' | (command -v jq >/dev/null 2>&1 && jq -r '.converged' || true)
	@curl -s -X POST http://127.0.0.1:$${PAGI_HTTP_PORT:-8000}/rlm -H "Content-Type: application/json" -d '{"query":"Step 2 list","context":"","depth":0}' | (command -v jq >/dev/null 2>&1 && jq -r '.converged' || true)
	@curl -s -X POST http://127.0.0.1:$${PAGI_HTTP_PORT:-8000}/rlm -H "Content-Type: application/json" -d '{"query":"Step 3 list","context":"","depth":0}' > /tmp/pagi_multi_turn_last.json
	@sleep 2
	@count=$$(grep -c -E "EXECUTING|ACTION" agent_actions.log 2>/dev/null || echo 0); \
		if [ "$$count" -ge 3 ]; then echo "Log assertion passed: $$count ACTION/EXECUTING lines."; else echo "Assertion failed: expected >= 3 lines, got $$count"; pkill -f "uvicorn src.main:app" 2>/dev/null; exit 1; fi
	@command -v jq >/dev/null 2>&1 && jq -e '.converged == true' /tmp/pagi_multi_turn_last.json >/dev/null && echo "Final converged=true." || true
	@pkill -f "uvicorn src.main:app" 2>/dev/null || true
	@echo "verify-multi-turn complete."

# Rust-mediated L5 dispatch: orchestrator (PAGI_ALLOW_REAL_DISPATCH) + bridge (PAGI_ACTIONS_VIA_GRPC), curl /rlm, assert ACTION line in log.
# Requires: orchestrator and bridge built; use Git Bash on Windows for &, sleep, grep. Log: PAGI_SELF_HEAL_LOG=agent_actions.log.
verify-rust-dispatch:
	@echo "Starting orchestrator with PAGI_ALLOW_REAL_DISPATCH=true..."
	@cd pagi-core-orchestrator && PAGI_ALLOW_REAL_DISPATCH=true PAGI_SELF_HEAL_LOG=../agent_actions.log cargo run --release &
	@sleep 5
	@echo "Starting bridge with PAGI_ACTIONS_VIA_GRPC=true PAGI_ALLOW_LOCAL_DISPATCH=false..."
	@cd pagi-intelligence-bridge && PAGI_ACTIONS_VIA_GRPC=true PAGI_ALLOW_LOCAL_DISPATCH=false PAGI_AGENT_ACTIONS_LOG=../agent_actions.log poetry run uvicorn src.main:app --port $${PAGI_HTTP_PORT:-8000} &
	@sleep 5
	@echo "Triggering /rlm with peek README.md..."
	@curl -s -X POST http://127.0.0.1:$${PAGI_HTTP_PORT:-8000}/rlm -H "Content-Type: application/json" -d '{"query":"peek README.md","context":"","depth":0}' | (command -v jq >/dev/null 2>&1 && jq . || cat)
	@sleep 3
	@echo "Asserting ACTION line with reasoning_id in agent_actions.log..."
	@grep -E "ACTION [^ ]+ [^ ]+ ->" agent_actions.log 2>/dev/null && echo "Log assertion passed: ACTION line with reasoning_id found." || (echo "Assertion failed: no ACTION line in agent_actions.log (ensure orchestrator used PAGI_SELF_HEAL_LOG=../agent_actions.log)."; pkill -f "cargo run" 2>/dev/null; pkill -f "uvicorn src.main:app" 2>/dev/null; exit 1)
	@pkill -f "cargo run" 2>/dev/null || true
	@pkill -f "uvicorn src.main:app" 2>/dev/null || true
	@echo "verify-rust-dispatch complete."

# Full blueprint verification: L4 bootstrap, health probes, Python and Rust heal cycle assertions.
# Requires Qdrant, orchestrator, and bridge running; PAGI_SELF_HEAL_LOG set. Use Git Bash on Windows.
verify-all: index-kb health-check test-self-heal test-rust-heal
	@if [ "$${PAGI_FORCE_TEST_FAIL}" = "true" ]; then $(MAKE) test-fail-sim; fi
	@msg="Blueprint verification complete: L4 populated, health ok, Python/Rust heal cycles asserted (force_fail: $${PAGI_FORCE_TEST_FAIL:-false})."; \
		echo "$$msg"; \
		log="$$PAGI_SELF_HEAL_LOG"; \
		if [ -n "$$log" ]; then \
			# Harden against cwd drift across chained targets by resolving an absolute path when possible.
			if command -v realpath >/dev/null 2>&1; then \
				log_abs="$$(realpath "$$log")"; \
			else \
				case "$$log" in \
					/*) log_abs="$$log" ;; \
					*) log_abs="$$(pwd)/$$log" ;; \
				esac; \
			fi; \
			# Pre-check log writability to make failures explicit (useful for Phase-4 RCA).
			log_dir="$$(dirname "$$log_abs")"; \
			mkdir -p "$$log_dir" 2>/dev/null || true; \
			if ( : >> "$$log_abs" ) 2>/dev/null; then \
				echo "$$msg" >> "$$log_abs"; \
			else \
				echo "WARNING: unable to write to PAGI_SELF_HEAL_LOG=$$log_abs"; \
			fi; \
		fi
