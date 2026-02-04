.PHONY: all build run test clean

all: build

build:
	cd pagi-core-orchestrator && cargo build --release
	cd pagi-intelligence-bridge && poetry install

run:
	# Run Rust orchestrator in background
	cd pagi-core-orchestrator && cargo run --release &
	# Run Python service
	cd pagi-intelligence-bridge && poetry run uvicorn src.main:app --reload

test:
	cd pagi-core-orchestrator && cargo test
	cd pagi-intelligence-bridge && poetry run pytest

clean:
	cd pagi-core-orchestrator && cargo clean
	rm -rf pagi-intelligence-bridge/.venv
