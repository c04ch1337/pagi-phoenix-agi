// Phase 4: Self-healing, Git-Watcher (Evolution Registry), propose/apply patch with HITL.

use std::io::Write;
use std::path::PathBuf;
use std::process::Command;
use std::sync::Arc;

use dashmap::DashMap;
use git2::{IndexAddOption, Repository, Signature};
use tonic::Status;
use uuid::Uuid;

use crate::memory_manager::MemoryManager;
use crate::proto::pagi_proto::{
    ApplyRequest, ApplyResponse, PatchRequest, PatchResponse, SearchRequest,
};

/// Pending patch stored after ProposePatch until ApplyPatch or expiry.
struct PendingPatch {
    proposed_code: String,
    requires_hitl: bool,
    component: String,
}

/// Watchdog: self-healing (RCA via L4), Git-Watcher for pagi-skills, patch propose/apply.
pub struct Watchdog {
    /// Evolution Registry path (pagi-skills/).
    registry_path: PathBuf,
    /// L4 for RCA search.
    memory: Arc<MemoryManager>,
    /// patch_id -> PendingPatch
    pending_patches: DashMap<String, PendingPatch>,
    /// Cargo/Pytest roots for test step (optional; default from cwd).
    core_dir: PathBuf,
    bridge_dir: PathBuf,
}

impl Watchdog {
    /// registry_path: e.g. ../pagi-skills from orchestrator dir.
    pub fn new(
        registry_path: PathBuf,
        memory: Arc<MemoryManager>,
        core_dir: PathBuf,
        bridge_dir: PathBuf,
    ) -> Arc<Self> {
        Arc::new(Self {
            registry_path,
            memory,
            pending_patches: DashMap::new(),
            core_dir,
            bridge_dir,
        })
    }

    fn open_repo(&self) -> Result<Repository, git2::Error> {
        if self.registry_path.exists() {
            Repository::open(&self.registry_path)
        } else {
            std::fs::create_dir_all(&self.registry_path)
                .map_err(|e| git2::Error::from_str(&e.to_string()))?;
            Repository::init(&self.registry_path)
        }
    }

    /// Git-Watcher: poll registry, commit changes. Run in tokio::spawn. Interval from PAGI_WATCH_INTERVAL_SECS.
    pub async fn watch_and_commit(self: Arc<Self>) {
        let secs = std::env::var("PAGI_WATCH_INTERVAL_SECS")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(60);
        let mut interval = tokio::time::interval(tokio::time::Duration::from_secs(secs));
        loop {
            interval.tick().await;
            if let Ok(repo) = self.open_repo() {
                if let Err(e) = self.commit_changes(&repo) {
                    eprintln!("[Watchdog] commit_changes: {}", e);
                }
            }
        }
    }

    fn commit_changes(&self, repo: &Repository) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let mut index = repo.index()?;
        index.add_all(["*"].iter(), IndexAddOption::DEFAULT, None)?;
        index.write()?;
        let tree_id = index.write_tree()?;
        let tree = repo.find_tree(tree_id)?;
        if let Ok(head) = repo.head() {
            let head_commit = head.peel_to_commit()?;
            if head_commit.tree_id() == tree_id {
                return Ok(());
            }
        }
        let head = repo.head();
        let parent = match head {
            Ok(r) => {
                let head_commit = r.peel_to_commit()?;
                vec![head_commit]
            }
            Err(_) => vec![],
        };
        let sig = Signature::now("Sovereign Architect", "agi@core")?;
        let msg = "Auto-commit self-patch (L6 traceability)";
        let _ = repo.commit(
            Some("HEAD"),
            &sig,
            &sig,
            msg,
            &tree,
            parent.iter().collect::<Vec<_>>().as_slice(),
        )?;
        Ok(())
    }

    /// Self-healing: RCA via L4 search, return proposed patch (stub code).
    pub async fn propose_patch(
        &self,
        req: PatchRequest,
    ) -> Result<PatchResponse, Status> {
        let search_req = SearchRequest {
            query: req.error_trace.clone(),
            kb_name: "kb_core".to_string(),
            limit: 5,
            query_vector: vec![],
        };
        let prior = self
            .memory
            .semantic_search(search_req)
            .await
            .map_err(|e| Status::internal(e.to_string()))?;

        let proposed_code = format!(
            "// Generic fix for: {}\n// Based on prior hits: {:?}",
            req.error_trace
                .lines()
                .next()
                .unwrap_or("")
                .chars()
                .take(200)
                .collect::<String>(),
            prior
                .hits
                .iter()
                .map(|h| &h.content_snippet)
                .take(2)
                .collect::<Vec<_>>()
        );

        let requires_hitl = req.component == "rust_core";
        let patch_id = Uuid::new_v4().to_string();
        self.pending_patches.insert(
            patch_id.clone(),
            PendingPatch {
                proposed_code: proposed_code.clone(),
                requires_hitl,
                component: req.component.clone(),
            },
        );

        Ok(PatchResponse {
            patch_id: patch_id.clone(),
            proposed_code,
            requires_hitl,
        })
    }

    /// Apply: HITL check, run tests, write patch to registry and commit.
    pub async fn apply_patch(
        &self,
        req: ApplyRequest,
    ) -> Result<ApplyResponse, Status> {
        let pending = self
            .pending_patches
            .get(&req.patch_id)
            .ok_or_else(|| Status::not_found("patch_id not found"))?;

        if pending.requires_hitl && !req.approved {
            return Err(Status::permission_denied(
                "HITL approval required for this patch",
            ));
        }

        let force_fail = std::env::var("PAGI_FORCE_TEST_FAIL")
            .ok()
            .map_or(false, |v| v.to_lowercase() == "true" || v == "1");
        if force_fail {
            return Err(Status::internal(
                "Forced test failure for verification",
            ));
        }

        // Run tests (generic: cargo test or pytest)
        let test_ok = if pending.component == "rust_core" {
            Command::new("cargo")
                .args(["test"])
                .current_dir(&self.core_dir)
                .output()
                .map(|o| o.status.success())
                .unwrap_or(false)
        } else {
            Command::new("poetry")
                .args(["run", "pytest", "tests/", "-v"])
                .current_dir(&self.bridge_dir)
                .output()
                .map(|o| o.status.success())
                .unwrap_or(false)
        };

        if !test_ok {
            return Err(Status::internal("Patch test failed; apply aborted"));
        }

        // Write proposed code to registry and commit
        let ext = if pending.component == "rust_core" {
            "rs"
        } else {
            "py"
        };
        let patches_dir = self.registry_path.join("patches");
        std::fs::create_dir_all(&patches_dir).map_err(|e| {
            Status::internal(format!("create patches dir: {}", e))
        })?;
        let patch_file = patches_dir.join(format!("patch_{}.{}", req.patch_id, ext));
        std::fs::write(&patch_file, &pending.proposed_code).map_err(|e| {
            Status::internal(format!("write patch file: {}", e))
        })?;

        let repo = self.open_repo().map_err(|e| {
            Status::internal(format!("open repo: {}", e))
        })?;
        let mut index = repo.index().map_err(|e| {
            Status::internal(format!("index: {}", e))
        })?;
        let rel = format!("patches/patch_{}.{}", req.patch_id, ext);
        index
            .add_path(std::path::Path::new(&rel))
            .map_err(|e| Status::internal(format!("add_path: {}", e)))?;
        index.write().map_err(|e| Status::internal(format!("index write: {}", e)))?;
        let tree_id = index.write_tree().map_err(|e| Status::internal(format!("write_tree: {}", e)))?;
        let tree = repo.find_tree(tree_id).map_err(|e| Status::internal(format!("find_tree: {}", e)))?;
        let head = repo.head();
        let parent = match head {
            Ok(r) => {
                let head_commit = r.peel_to_commit().map_err(|e| Status::internal(e.to_string()))?;
                vec![head_commit]
            }
            Err(_) => vec![],
        };
        let sig = Signature::now("Sovereign Architect", "agi@core")
            .map_err(|e| Status::internal(e.to_string()))?;
        let msg = format!("Apply patch {} ({})", req.patch_id, pending.component);
        let commit = repo
            .commit(
                Some("HEAD"),
                &sig,
                &sig,
                &msg,
                &tree,
                parent.iter().collect::<Vec<_>>().as_slice(),
            )
            .map_err(|e| Status::internal(e.to_string()))?;
        let commit_hash = repo
            .find_commit(commit)
            .map_err(|e| Status::internal(e.to_string()))?
            .id()
            .to_string();

        self.pending_patches.remove(&req.patch_id);

        Ok(ApplyResponse {
            success: true,
            commit_hash,
        })
    }

    /// Legacy SelfHeal RPC: propose only (no apply).
    pub fn propose_heal(&self, _error_trace: &str) -> (String, bool) {
        (String::new(), false)
    }

    /// Simulation: run propose → apply; with PAGI_FORCE_TEST_FAIL use approved=true to hit force_fail path.
    pub async fn simulate_error(&self) -> Result<crate::proto::pagi_proto::Empty, Status> {
        let error_trace = "Simulated Rust error for verification".to_string();
        let component = "rust_core".to_string();
        let req = PatchRequest {
            error_trace: error_trace.clone(),
            component: component.clone(),
        };
        let propose_resp = self.propose_patch(req).await?;

        let force_fail = std::env::var("PAGI_FORCE_TEST_FAIL")
            .ok()
            .map_or(false, |v| v.to_lowercase() == "true" || v == "1");
        let approved = force_fail; // When forcing fail, pass HITL so apply_patch hits the force_fail return

        let apply_req = ApplyRequest {
            patch_id: propose_resp.patch_id,
            approved,
            component: component.clone(),
            requires_hitl: propose_resp.requires_hitl,
        };
        let _apply_result = self.apply_patch(apply_req).await;
        // Expected: Err(permission_denied) when !force_fail, or Err(internal) when force_fail. We do not surface it; simulation succeeded.

        let log_path = std::env::var("PAGI_SELF_HEAL_LOG").unwrap_or_else(|_| "agent_actions.log".into());
        if let Ok(mut f) = std::fs::OpenOptions::new().append(true).create(true).open(&log_path) {
            let _ = writeln!(f, "Heal cycle simulated");
        }

        Ok(crate::proto::pagi_proto::Empty {})
    }
}
