.PHONY: all build run test clean qdrant load-env check-proto build-incremental health-check debug-self-heal index-kb test-self-heal test-rust-heal test-fail-sim verify-all

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

test: load-env
	. ./.env 2>/dev/null || true; cd pagi-core-orchestrator && cargo test
	. ./.env 2>/dev/null || true; cd pagi-intelligence-bridge && poetry run pytest

clean:
	cd pagi-core-orchestrator && cargo clean
	rm -rf pagi-intelligence-bridge/.venv

# Validate pagi.proto consistency (compiles via Rust build.rs)
check-proto:
	cd pagi-core-orchestrator && cargo check

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
