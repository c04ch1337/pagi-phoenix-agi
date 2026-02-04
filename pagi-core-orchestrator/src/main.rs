// Phoenix AGI (pagi) — Rust backbone: gRPC orchestrator, memory, watchdog.

mod memory_manager;
mod proto;
mod safety_governor;
mod watchdog;

use memory_manager::MemoryManager;
use proto::pagi_proto::pagi_server::{Pagi, PagiServer};
use proto::pagi_proto::{
    ActionRequest, ActionResponse, ApplyRequest, ApplyResponse, Empty, HealRequest, HealResponse,
    MemoryRequest, MemoryResponse, PatchRequest, PatchResponse, RlmRequest, RlmResponse,
    SearchRequest, SearchResponse, UpsertRequest, UpsertResponse,
};
use safety_governor::SafetyGovernor;
use std::path::PathBuf;
use std::sync::Arc;
use tonic::{Request, Response, Status};
use watchdog::Watchdog;

struct Orchestrator {
    memory: Arc<MemoryManager>,
    watchdog: Arc<Watchdog>,
    safety_governor: SafetyGovernor,
}

#[tonic::async_trait]
impl Pagi for Orchestrator {
    async fn access_memory(
        &self,
        request: Request<MemoryRequest>,
    ) -> Result<Response<MemoryResponse>, Status> {
        let req = request.into_inner();
        let value = if req.value.is_empty() {
            None
        } else {
            Some(req.value.as_str())
        };
        let (data, success) = self.memory.access(req.layer, &req.key, value);
        Ok(Response::new(MemoryResponse { data, success }))
    }

    async fn delegate_rlm(
        &self,
        request: Request<RlmRequest>,
    ) -> Result<Response<RlmResponse>, Status> {
        let guarded_req = self.safety_governor.guard_rlm(request).await?;
        let req = guarded_req.into_inner();
        // TODO: forward to Python RLM via sidecar or pyo3
        Ok(Response::new(RlmResponse {
            summary: "Generic delegation processed".to_string(),
            converged: (req.depth as u32) <= self.safety_governor.max_depth,
        }))
    }

    async fn execute_action(
        &self,
        request: Request<ActionRequest>,
    ) -> Result<Response<ActionResponse>, Status> {
        let req = request.into_inner();

        // Mirror recursion circuit-breaker semantics used by guard_rlm without introducing new schema drift.
        if (req.depth as u32) > self.safety_governor.max_depth {
            return Err(Status::invalid_argument(
                "Recursion depth exceeded; circuit breaker activated",
            ));
        }

        // PAGI_MOCK_MODE precedence: mock path when request asks for mock or env forces mock.
        let env_mock = std::env::var("PAGI_MOCK_MODE")
            .map(|v| v.trim().eq_ignore_ascii_case("true") || v == "1")
            .unwrap_or(false);
        if req.mock_mode || env_mock {
            let skill = req.skill_name;
            return Ok(Response::new(ActionResponse {
                observation: format!("Observation: mock executed skill={skill}"),
                success: true,
                error: "".to_string(),
            }));
        }

        // Real dispatch only when explicitly enabled (allow-list, timeout, no shell).
        let allow_real = std::env::var("PAGI_ALLOW_REAL_DISPATCH")
            .map(|v| v.trim().eq_ignore_ascii_case("true") || v == "1")
            .unwrap_or(false);
        if allow_real {
            return self
                .watchdog
                .execute_action_real(req)
                .await
                .map(Response::new);
        }

        // PAGI_ALLOW_REAL_DISPATCH != true → return mock observation (do not expose unimplemented).
        let skill = req.skill_name;
        Ok(Response::new(ActionResponse {
            observation: format!("Observation: mock executed skill={skill}"),
            success: true,
            error: "".to_string(),
        }))
    }

    async fn self_heal(
        &self,
        request: Request<HealRequest>,
    ) -> Result<Response<HealResponse>, Status> {
        let req = request.into_inner();
        let (proposed_patch, auto_apply) = self.watchdog.propose_heal(&req.error_trace);
        Ok(Response::new(HealResponse {
            proposed_patch,
            auto_apply,
        }))
    }

    async fn semantic_search(
        &self,
        request: Request<SearchRequest>,
    ) -> Result<Response<SearchResponse>, Status> {
        self.memory
            .semantic_search(request.into_inner())
            .await
            .map(Response::new)
    }

    async fn propose_patch(
        &self,
        request: Request<PatchRequest>,
    ) -> Result<Response<PatchResponse>, Status> {
        self.watchdog
            .propose_patch(request.into_inner())
            .await
            .map(Response::new)
    }

    async fn apply_patch(
        &self,
        request: Request<ApplyRequest>,
    ) -> Result<Response<ApplyResponse>, Status> {
        self.watchdog
            .apply_patch(request.into_inner())
            .await
            .map(Response::new)
    }

    async fn upsert_vectors(
        &self,
        request: Request<UpsertRequest>,
    ) -> Result<Response<UpsertResponse>, Status> {
        self.memory
            .upsert_vectors(request.into_inner())
            .await
            .map(Response::new)
    }

    async fn simulate_error(
        &self,
        _request: Request<Empty>,
    ) -> Result<Response<Empty>, Status> {
        self.watchdog.simulate_error().await.map(Response::new)
    }
}

fn default_paths() -> (PathBuf, PathBuf, PathBuf) {
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let registry = std::env::var("PAGI_REGISTRY_PATH")
        .map(PathBuf::from)
        .unwrap_or_else(|_| cwd.join("../pagi-skills"));
    let core_dir = std::env::var("PAGI_CORE_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| cwd.clone());
    let bridge_dir = std::env::var("PAGI_BRIDGE_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| cwd.join("../pagi-intelligence-bridge"));
    (registry, core_dir, bridge_dir)
}

fn grpc_addr() -> std::net::SocketAddr {
    let port = std::env::var("PAGI_GRPC_PORT")
        .unwrap_or_else(|_| "50051".into())
        .parse::<u16>()
        .unwrap_or(50051);
    format!("[::1]:{}", port)
        .parse()
        .unwrap_or_else(|_| "[::1]:50051".parse().unwrap())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::proto::pagi_proto::ActionRequest;
    use std::collections::HashMap;
    use tonic::Request;

    #[tokio::test]
    async fn test_execute_action_mock() {
        std::env::set_var("PAGI_DISABLE_QDRANT", "1");
        std::env::set_var("PAGI_MOCK_MODE", "true");
        std::env::set_var("PAGI_ALLOW_REAL_DISPATCH", "false");

        let (registry, core_dir, bridge_dir) = default_paths();
        let memory = MemoryManager::new_async().await.unwrap();
        let watchdog = Watchdog::new(registry, memory.clone(), core_dir, bridge_dir);
        let gov = SafetyGovernor::default();
        let orch = Orchestrator {
            memory,
            watchdog,
            safety_governor: gov,
        };
        let req = Request::new(ActionRequest {
            skill_name: "peek_file".to_string(),
            params: HashMap::new(),
            depth: 0,
            reasoning_id: "r1".to_string(),
            mock_mode: true,
            allow_list_hash: String::new(),
            timeout_ms: 0,
        });
        let resp = orch.execute_action(req).await.unwrap();
        let inner = resp.into_inner();
        assert!(inner.success);
        assert!(inner.observation.contains("mock executed"));
        assert!(inner.observation.contains("peek_file"));

        std::env::remove_var("PAGI_MOCK_MODE");
        std::env::remove_var("PAGI_ALLOW_REAL_DISPATCH");
        std::env::remove_var("PAGI_DISABLE_QDRANT");
    }

    #[tokio::test]
    async fn test_execute_action_fallback_mock_when_real_disabled() {
        std::env::set_var("PAGI_DISABLE_QDRANT", "1");
        std::env::set_var("PAGI_ALLOW_REAL_DISPATCH", "false");
        std::env::remove_var("PAGI_MOCK_MODE");

        let (registry, core_dir, bridge_dir) = default_paths();
        let memory = MemoryManager::new_async().await.unwrap();
        let watchdog = Watchdog::new(registry, memory.clone(), core_dir, bridge_dir);
        let gov = SafetyGovernor::default();
        let orch = Orchestrator {
            memory,
            watchdog,
            safety_governor: gov,
        };
        let req = Request::new(ActionRequest {
            skill_name: "unknown_skill".to_string(),
            params: HashMap::new(),
            depth: 0,
            reasoning_id: "r1".to_string(),
            mock_mode: false,
            allow_list_hash: String::new(),
            timeout_ms: 0,
        });
        let resp = orch.execute_action(req).await.unwrap();
        let inner = resp.into_inner();
        assert!(inner.success);
        assert!(inner.observation.contains("mock executed"));
        assert!(inner.observation.contains("unknown_skill"));

        std::env::remove_var("PAGI_ALLOW_REAL_DISPATCH");
        std::env::remove_var("PAGI_DISABLE_QDRANT");
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    if std::env::var("RUST_LOG").is_err() {
        std::env::set_var(
            "RUST_LOG",
            std::env::var("PAGI_LOG_LEVEL").unwrap_or_else(|_| "info".into()),
        );
    }
    let _ = env_logger::Builder::from_default_env().try_init();

    let addr = grpc_addr();
    let memory = MemoryManager::new_async().await?;
    memory.init_kbs().await?;
    let (registry_path, core_dir, bridge_dir) = default_paths();
    let watchdog = Watchdog::new(registry_path, memory.clone(), core_dir, bridge_dir);
    let watchdog_clone = Arc::clone(&watchdog);
    tokio::spawn(async move {
        watchdog_clone.watch_and_commit().await;
    });
    let safety_governor = SafetyGovernor::new();
    let orchestrator = Orchestrator {
        memory,
        watchdog,
        safety_governor,
    };
    tonic::transport::Server::builder()
        .add_service(PagiServer::new(orchestrator))
        .serve(addr)
        .await?;
    Ok(())
}
