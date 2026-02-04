# Phoenix AGI — Shared Contract

This folder holds **frontend-facing types** that must stay synchronized with the backend and docs.

| Asset | Purpose |
|-------|--------|
| **types.ts** | TypeScript types for API requests/responses and WebSocket AgentEvent payloads. Use in desktop/frontend apps. |
| **../docs/Boilerplate-Contract.md** | Formal contract (KB names, event kinds, request/response shapes). |
| **../docs/Backend-Integration-Guide.md** | Full backend integration guide (API/IPC, WebSocket, memory layers, deployment). |
| **pagi-intelligence-bridge/src/mock_provider.py** | Mock backend implementing the same contract; run with `uvicorn src.mock_provider:app --port 8001`. |

When changing the contract, update in order: (1) `docs/Boilerplate-Contract.md`, (2) `contract/types.ts`, (3) `mock_provider.py`.
