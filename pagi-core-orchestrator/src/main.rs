// Phoenix AGI (pagi) — Rust backbone: gRPC orchestrator, memory, watchdog.

mod memory_manager;
mod proto;
mod safety_governor;
mod watchdog;

use memory_manager::MemoryManager;
use proto::pagi_proto::pagi_server::{Pagi, PagiServer};
use proto::pagi_proto::{
    HealRequest, HealResponse, MemoryRequest, MemoryResponse, RlmRequest, RlmResponse,
};
use safety_governor::SafetyGovernor;
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
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let addr = "[::1]:50051".parse()?;
    let memory = MemoryManager::new();
    let watchdog = Watchdog::new();
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
