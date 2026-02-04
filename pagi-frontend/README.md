# pagi-frontend

Paste your **Google AI Studio** frontend here and connect it to the Pagi gRPC and FastAPI APIs.

- **HTTP (FastAPI):** `POST /rlm`, `POST /rlm-multi-turn`, `GET /health` — use `PAGI_HTTP_PORT` (default 8000).
- **gRPC (orchestrator):** Memory, actions, ProposePatch/ApplyPatch — use `PAGI_GRPC_ADDR` (default `[::1]:50051`).

After copying in your UI, run from repo root: `make run-frontend` (or `cd pagi-frontend && npm start`).
