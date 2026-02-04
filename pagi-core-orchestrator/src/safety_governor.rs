// Generic CORE SafetyGovernor: recursion limits, HITL gates, basic sanitization.
// No Red/Blue or adversarial elements; extensibility hooks for future verticals.

use tonic::{Request, Status};

use crate::proto::pagi_proto::{HealRequest, RlmRequest};

pub struct SafetyGovernor {
    /// Configurable via env or config.toml in future verticals.
    pub max_depth: u32,
    /// Toggle for human approval on critical ops.
    pub hitl_gate: bool,
}

impl SafetyGovernor {
    pub fn new() -> Self {
        let max_depth = std::env::var("PAGI_MAX_RECURSION_DEPTH")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(5);
        let hitl_gate = std::env::var("PAGI_HITL_GATE")
            .ok()
            .and_then(|s| match s.to_lowercase().as_str() {
                "true" | "1" | "yes" => Some(true),
                "false" | "0" | "no" => Some(false),
                _ => s.parse().ok(),
            })
            .unwrap_or(true);
        Self { max_depth, hitl_gate }
    }

    /// Middleware: Enforce recursion limit and basic sanitization.
    pub async fn guard_rlm(
        &self,
        req: Request<RlmRequest>,
    ) -> Result<Request<RlmRequest>, Status> {
        let msg = req.into_inner();
        if (msg.depth as u32) > self.max_depth {
            return Err(Status::invalid_argument(
                "Recursion depth exceeded; circuit breaker activated",
            ));
        }

        let sanitized_query = self.sanitize(&msg.sub_query);
        let sanitized_context = self.sanitize(&msg.sub_context);

        if self.hitl_gate && msg.sub_query.contains("patch_core") {
            return Err(Status::permission_denied(
                "HITL approval required for core operations",
            ));
        }

        Ok(Request::new(RlmRequest {
            sub_query: sanitized_query,
            sub_context: sanitized_context,
            depth: msg.depth,
        }))
    }

    fn sanitize(&self, input: &str) -> String {
        input.trim().chars().take(1024 * 10).collect()
    }

    /// Placeholder for heal guard: extend in Phase 4 without adversarial elements.
    #[allow(dead_code)]
    pub async fn guard_heal(&self, _req: &HealRequest) -> Result<(), Status> {
        // Invoke local tests pre-apply
        unimplemented!()
    }
}

impl Default for SafetyGovernor {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tonic::Request;

    #[tokio::test]
    async fn guard_rlm_rejects_depth_over_max() {
        let gov = SafetyGovernor::new();
        let req = Request::new(RlmRequest {
            sub_query: "test".to_string(),
            sub_context: "ctx".to_string(),
            depth: 6,
        });
        let result = gov.guard_rlm(req).await;
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert_eq!(err.code(), tonic::Code::InvalidArgument);
    }

    #[tokio::test]
    async fn guard_rlm_allows_depth_at_max() {
        let gov = SafetyGovernor::new();
        let req = Request::new(RlmRequest {
            sub_query: "ok".to_string(),
            sub_context: "ctx".to_string(),
            depth: 5,
        });
        let result = gov.guard_rlm(req).await;
        assert!(result.is_ok());
        let guarded = result.unwrap().into_inner();
        assert_eq!(guarded.depth, 5);
        assert_eq!(guarded.sub_query, "ok");
    }

    #[tokio::test]
    async fn guard_rlm_sanitizes_trim_and_length() {
        let gov = SafetyGovernor::new();
        let long: String = "a".repeat(20_000);
        let req = Request::new(RlmRequest {
            sub_query: format!("  {}  ", long),
            sub_context: "ctx".to_string(),
            depth: 0,
        });
        let result = gov.guard_rlm(req).await;
        assert!(result.is_ok());
        let guarded = result.unwrap().into_inner();
        assert!(guarded.sub_query.len() <= 10240);
        assert!(!guarded.sub_query.starts_with(' '));
    }

    #[tokio::test]
    async fn guard_rlm_denies_patch_core_when_hitl_gate_on() {
        let gov = SafetyGovernor::new();
        let req = Request::new(RlmRequest {
            sub_query: "patch_core apply".to_string(),
            sub_context: "".to_string(),
            depth: 0,
        });
        let result = gov.guard_rlm(req).await;
        assert!(result.is_err());
        assert_eq!(result.unwrap_err().code(), tonic::Code::PermissionDenied);
    }
}
