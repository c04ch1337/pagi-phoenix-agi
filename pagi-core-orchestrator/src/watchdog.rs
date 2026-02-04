// Phase 4: Self-healing, Git-Watcher (Evolution Registry), propose/apply patch with HITL.
// L5 real dispatch: allow-list from bridge src/skills, subprocess with timeout, no shell.

use std::collections::HashMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command as StdCommand;
use std::sync::Arc;

use dashmap::DashMap;
use git2::{IndexAddOption, Repository, Signature};
use sha2::{Digest, Sha256};
use tonic::Status;
use uuid::Uuid;

use crate::memory_manager::MemoryManager;
use crate::proto::pagi_proto::{
    ActionRequest, ActionResponse, ApplyRequest, ApplyResponse, PatchRequest, PatchResponse,
    SearchRequest,
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

    /// Open bridge dir as Git repo (for auto-evolved skill commit).
    fn open_bridge_repo(&self) -> Result<Repository, git2::Error> {
        if self.bridge_dir.join(".git").exists() {
            Repository::open(&self.bridge_dir)
        } else {
            Err(git2::Error::from_str("bridge dir is not a git repo"))
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

    /// Load allow-list of skill names from bridge src/skills: .py files only, exclude __init__.py.
    /// Prefer Git tree (tracked files only); fallback to read_dir.
    fn load_skills_allow_list(&self) -> Result<Vec<String>, String> {
        let skills_dir = self.bridge_dir.join("src").join("skills");
        let mut names: Vec<String> = Vec::new();

        if let Ok(repo) = Repository::discover(&self.bridge_dir) {
            if let Ok(workdir) = repo.workdir().ok_or_else(|| "no workdir".to_string()) {
                let workdir = workdir.to_path_buf();
                if let Ok(rel) = skills_dir.strip_prefix(&workdir) {
                    let rel_str = rel.to_string_lossy().replace('\\', "/");
                    if let Ok(head) = repo.head() {
                        if let Ok(commit) = head.peel_to_commit() {
                            if let Ok(root_tree) = commit.tree() {
                                if let Ok(entry) = root_tree.get_path(Path::new(&rel_str)) {
                                    if let Ok(obj) = entry.to_object(&repo) {
                                        if let Ok(tree) = obj.peel_to_tree() {
                                            for e in tree.iter() {
                                                if let Some(n) = e.name() {
                                                    if n.ends_with(".py") && n != "__init__.py" {
                                                        if let Some(stem) = n.strip_suffix(".py") {
                                                            names.push(stem.to_string());
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        if names.is_empty() {
            if let Ok(rd) = std::fs::read_dir(&skills_dir) {
                for e in rd.flatten() {
                    if let Some(n) = e.file_name().to_str() {
                        if n.ends_with(".py") && n != "__init__.py" {
                            if let Some(stem) = n.strip_suffix(".py") {
                                names.push(stem.to_string());
                            }
                        }
                    }
                }
            }
        }

        names.sort();
        Ok(names)
    }

    /// SHA256 hex of sorted allow-list (one name per line) for consistency check.
    fn allow_list_hash(skills: &[String]) -> String {
        let mut hasher = Sha256::new();
        for s in skills {
            hasher.update(s.as_bytes());
            hasher.update(b"\n");
        }
        format!("{:x}", hasher.finalize())
    }

    fn env_truthy(name: &str, default: bool) -> bool {
        std::env::var(name)
            .ok()
            .map(|v| {
                let v = v.trim().to_lowercase();
                v == "true" || v == "1" || v == "yes" || v == "y" || v == "on"
            })
            .unwrap_or(default)
    }

    fn sanitize_skill_filename(raw: &str) -> String {
        // Defense-in-depth: strip path separators, collapse to [A-Za-z0-9_-.], ensure .py.
        let mut s = raw.trim().replace(['/', '\\'], "_");
        s = s.replace("..", "");
        s = s
            .chars()
            .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.'))
            .collect::<String>();
        if s.is_empty() {
            s = "evolved_skill.py".to_string();
        }
        if !s.ends_with(".py") {
            s.push_str(".py");
        }
        s
    }

    /// After a successful *Python* self-patch apply (and auto-commit), run evolve_skill_from_patch and commit in bridge.
    ///
    /// Constraints:
    /// - Gated by PAGI_AUTO_EVOLVE_SKILLS
    /// - Uses existing ExecuteAction/allow-list machinery (no new proto)
    /// - Single call to evolve_skill_from_patch; parse EVOLVED_PATH from observation; git add/commit in bridge repo
    async fn propose_new_skill_from_patch(&self, patch_path: &Path) -> Result<(), Status> {
        let patch_content = std::fs::read_to_string(patch_path)
            .map_err(|e| Status::internal(format!("read patch: {}", e)))?;

        let allow_list = self
            .load_skills_allow_list()
            .map_err(|e| Status::internal(format!("load allow-list: {}", e)))?;

        let mut params = HashMap::new();
        params.insert("patch_content".to_string(), patch_content);
        let evolve_req = ActionRequest {
            skill_name: "evolve_skill_from_patch".to_string(),
            params,
            depth: 0,
            reasoning_id: format!("auto-evolve-{}", Uuid::new_v4()),
            mock_mode: false,
            allow_list_hash: Self::allow_list_hash(&allow_list),
            timeout_ms: 15_000,
        };

        let evolve_resp = self.execute_action_real(evolve_req).await?;
        if !evolve_resp.success {
            return Err(Status::internal(format!(
                "evolve_skill_from_patch failed: {}",
                evolve_resp.error
            )));
        }

        let obs = evolve_resp.observation.trim();
        const PREFIX: &str = "EVOLVED_PATH:";
        let rel_path = obs
            .strip_prefix(PREFIX)
            .map(|s| s.trim())
            .filter(|s| !s.is_empty())
            .ok_or_else(|| {
                Status::internal(format!(
                    "evolve_skill_from_patch observation missing EVOLVED_PATH: {:?}",
                    obs.chars().take(80).collect::<String>()
                ))
            })?;
        let rel_path = rel_path.replace('\\', "/");

        let repo = self
            .open_bridge_repo()
            .map_err(|e| Status::internal(format!("open bridge repo: {}", e)))?;
        let mut index = repo
            .index()
            .map_err(|e| Status::internal(format!("bridge index: {}", e)))?;
        index
            .add_path(Path::new(&rel_path))
            .map_err(|e| Status::internal(format!("bridge add_path: {}", e)))?;
        index
            .write()
            .map_err(|e| Status::internal(format!("bridge index write: {}", e)))?;
        let tree_id = index
            .write_tree()
            .map_err(|e| Status::internal(format!("bridge write_tree: {}", e)))?;
        let tree = repo
            .find_tree(tree_id)
            .map_err(|e| Status::internal(format!("bridge find_tree: {}", e)))?;

        let parent = match repo.head() {
            Ok(r) => vec![r
                .peel_to_commit()
                .map_err(|e| Status::internal(e.to_string()))?],
            Err(_) => vec![],
        };
        let sig = Signature::now("Sovereign Architect", "agi@core")
            .map_err(|e| Status::internal(e.to_string()))?;
        let msg = "Auto-evolved skill from self-patch";
        let _ = repo
            .commit(
                Some("HEAD"),
                &sig,
                &sig,
                msg,
                &tree,
                parent.iter().collect::<Vec<_>>().as_slice(),
            )
            .map_err(|e| Status::internal(e.to_string()))?;

        Ok(())
    }

    /// Real L5 dispatch: allow-list check, hash check, spawn python skill with timeout, log, return.
    /// No shell; timeout hard-enforced. Logs to PAGI_AGENT_ACTIONS_LOG (or PAGI_SELF_HEAL_LOG).
    pub async fn execute_action_real(
        &self,
        req: ActionRequest,
    ) -> Result<ActionResponse, Status> {
        let allow_list = self
            .load_skills_allow_list()
            .map_err(|e| Status::internal(format!("load allow-list: {}", e)))?;

        if !allow_list.contains(&req.skill_name) {
            return Err(Status::permission_denied("Skill not in registry"));
        }

        let computed_hash = Self::allow_list_hash(&allow_list);
        if !req.allow_list_hash.is_empty() && req.allow_list_hash != computed_hash {
            return Err(Status::invalid_argument("Allow-list mismatch"));
        }

        let timeout_ms = if req.timeout_ms > 0 {
            req.timeout_ms
        } else {
            5000
        };
        let runner_script = self.bridge_dir.join("scripts").join("run_skill.py");
        if !runner_script.exists() {
            return Err(Status::not_found(format!(
                "Runner script not found: {}",
                runner_script.display()
            )));
        }

        let params_json: String = {
            let map: HashMap<&str, &str> = req
                .params
                .iter()
                .map(|(k, v)| (k.as_str(), v.as_str()))
                .collect();
            serde_json::to_string(&map).unwrap_or_else(|_| "{}".to_string())
        };

        let skill_name = req.skill_name.clone();
        let reasoning_id = req.reasoning_id.clone();
        let timeout_dur = std::time::Duration::from_millis(timeout_ms as u64);

        let child = tokio::process::Command::new("python")
            .arg(&runner_script)
            .arg(&req.skill_name)
            .arg(&params_json)
            .current_dir(&self.bridge_dir)
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .map_err(|e| Status::internal(format!("spawn python: {}", e)))?;

        let child = Arc::new(tokio::sync::Mutex::new(Some(child)));
        let child_timeout = Arc::clone(&child);
        let (observation, success, error_msg) = tokio::select! {
            res = async move {
                let c = child.lock().await.take().unwrap();
                c.wait_with_output().await
            } => match res {
                Ok(output) => {
                    let observation = String::from_utf8_lossy(&output.stdout).trim().to_string();
                    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
                    let success = output.status.success();
                    let error_msg = if success {
                        String::new()
                    } else if stderr.is_empty() {
                        format!("exit code {:?}", output.status.code())
                    } else {
                        stderr
                    };
                    (observation, success, error_msg)
                }
                Err(e) => return Err(Status::internal(format!("wait_with_output: {}", e))),
            },
            _ = tokio::time::sleep(timeout_dur) => {
                if let Some(mut c) = child_timeout.lock().await.take() {
                    let _ = c.start_kill();
                    let _ = c.wait().await;
                }
                (
                    String::new(),
                    false,
                    "Execution timed out".to_string(),
                )
            }
        };

        let log_path = std::env::var("PAGI_AGENT_ACTIONS_LOG")
            .or_else(|_| std::env::var("PAGI_SELF_HEAL_LOG"))
            .unwrap_or_else(|_| "agent_actions.log".into());
        if let Ok(mut f) = std::fs::OpenOptions::new()
            .append(true)
            .create(true)
            .open(&log_path)
        {
            let log_line = if success {
                format!("ACTION {} {} -> {}", reasoning_id, skill_name, observation)
            } else {
                format!("ACTION {} {} -> {}", reasoning_id, skill_name, error_msg)
            };
            let _ = writeln!(f, "{}", log_line);
        }

        Ok(ActionResponse {
            observation,
            success,
            error: error_msg,
        })
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

    /// Path to HITL approve flag file (e.g. approve.patch in core dir). Presence enables apply for core patches.
    fn approve_flag_path(&self) -> PathBuf {
        let name = std::env::var("PAGI_APPROVE_FLAG").unwrap_or_else(|_| "approve.patch".into());
        self.core_dir.join(name)
    }

    /// Check if HITL approve flag file exists (poll for human-in-the-loop).
    fn hitl_approved_via_flag(&self) -> bool {
        self.approve_flag_path().exists()
    }

    /// Apply: HITL check (request approved or approve-flag file present), run tests, write patch to registry and commit.
    pub async fn apply_patch(
        &self,
        req: ApplyRequest,
    ) -> Result<ApplyResponse, Status> {
        let pending = self
            .pending_patches
            .get(&req.patch_id)
            .ok_or_else(|| Status::not_found("patch_id not found"))?;

        let approved = req.approved || (pending.requires_hitl && self.hitl_approved_via_flag());
        if pending.requires_hitl && !approved {
            return Err(Status::permission_denied(
                "HITL approval required for this patch (set approved or create PAGI_APPROVE_FLAG file)",
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

        // Skip test step when set (e.g. test_apply_patch_auto_commit); not for production.
        let skip_apply_test = std::env::var("PAGI_SKIP_APPLY_TEST")
            .ok()
            .map_or(false, |v| v.to_lowercase() == "true" || v == "1");

        // Run tests (generic: cargo test or pytest)
        let test_ok = if skip_apply_test {
            true
        } else if pending.component == "rust_core" {
            StdCommand::new("cargo")
                .args(["test"])
                .current_dir(&self.core_dir)
                .output()
                .map(|o| o.status.success())
                .unwrap_or(false)
        } else {
            StdCommand::new("poetry")
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

        let auto_commit = Self::env_truthy("PAGI_AUTO_COMMIT_SELF_PATCH", true);

        let commit_hash = if auto_commit {
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
            let msg = format!("Self-patch apply {} for {}", req.patch_id, pending.component);
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
            let hash = repo
                .find_commit(commit)
                .map_err(|e| Status::internal(e.to_string()))?
                .id()
                .to_string();
            hash
        } else {
            String::new()
        };

        // Auto-evolve: after python_skill apply *and* auto-commit, propose and persist a new skill from the patch.
        // Gate: PAGI_AUTO_EVOLVE_SKILLS=true.
        let auto_evolve = Self::env_truthy("PAGI_AUTO_EVOLVE_SKILLS", false);
        if auto_commit && auto_evolve && pending.component == "python_skill" {
            // Best-effort: if evolution fails, do not fail the patch apply.
            let _ = self.propose_new_skill_from_patch(&patch_file).await;
        }

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

    /// Simulation: run propose → optionally poll for HITL approve flag → apply. With PAGI_FORCE_TEST_FAIL use approved=true to hit force_fail path.
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
        let mut approved = force_fail; // When forcing fail, pass HITL so apply_patch hits the force_fail return

        // When HITL required and not force_fail, poll for approve flag file (e.g. approve.patch) before apply.
        if propose_resp.requires_hitl && !approved {
            let poll_secs: u64 = std::env::var("PAGI_HITL_POLL_SECS")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(30);
            let step = std::time::Duration::from_secs(1);
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(poll_secs);
            while std::time::Instant::now() < deadline {
                if self.hitl_approved_via_flag() {
                    approved = true;
                    break;
                }
                tokio::time::sleep(step).await;
            }
        }

        let apply_req = ApplyRequest {
            patch_id: propose_resp.patch_id,
            approved,
            component: component.clone(),
            requires_hitl: propose_resp.requires_hitl,
        };
        let _apply_result = self.apply_patch(apply_req).await;
        // Expected: Err(permission_denied) when !approved, or Err(internal) when force_fail. We do not surface it; simulation succeeded.

        let log_path = std::env::var("PAGI_SELF_HEAL_LOG").unwrap_or_else(|_| "agent_actions.log".into());
        if let Ok(mut f) = std::fs::OpenOptions::new().append(true).create(true).open(&log_path) {
            let _ = writeln!(f, "Heal cycle simulated");
        }

        Ok(crate::proto::pagi_proto::Empty {})
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::proto::pagi_proto::{ActionRequest, ApplyRequest, PatchRequest};
    use std::collections::HashMap;
    use std::fs;
    use std::path::PathBuf;
    use std::sync::{Mutex, OnceLock};

    static TEST_ENV_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

    fn lock_test_env() -> std::sync::MutexGuard<'static, ()> {
        // Tests in this module mutate global process env vars.
        // `cargo test` runs tests in parallel by default, so serialize to avoid cross-test interference/hangs.
        TEST_ENV_LOCK
            .get_or_init(|| Mutex::new(()))
            .lock()
            .expect("lock TEST_ENV_LOCK")
    }

    fn temp_bridge_dir(skills: &[&str], run_script_sleep: bool) -> PathBuf {
        let temp = std::env::temp_dir().join(format!("pagi_watchdog_test_{}", uuid::Uuid::new_v4()));
        let skills_dir = temp.join("src").join("skills");
        let scripts_dir = temp.join("scripts");
        fs::create_dir_all(&skills_dir).unwrap();
        fs::create_dir_all(&scripts_dir).unwrap();
        for name in skills {
            let path = skills_dir.join(format!("{}.py", name));
            fs::write(path, "# test stub\n").unwrap();
        }
        let run_content = if run_script_sleep {
            "import sys, time\nname = sys.argv[1] if len(sys.argv) > 1 else ''\nif name == 'sleep':\n  time.sleep(100)\nelse:\n  print('ok')\n"
        } else {
            "import sys\nprint('ok')\n"
        };
        fs::write(scripts_dir.join("run_skill.py"), run_content).unwrap();
        temp
    }

    #[tokio::test]
    async fn test_execute_action_unknown_skill() {
        let _g = lock_test_env();
        std::env::set_var("PAGI_DISABLE_QDRANT", "1");
        let temp = temp_bridge_dir(&["peek_file"], false);
        let registry = temp.join("registry");
        fs::create_dir_all(&registry).unwrap();
        let memory = MemoryManager::new_async().await.unwrap();
        let core_dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        let watchdog = Watchdog::new(registry, memory, core_dir, temp.clone());
        let req = ActionRequest {
            skill_name: "skill_not_in_registry".to_string(),
            params: HashMap::new(),
            depth: 0,
            reasoning_id: "r1".to_string(),
            mock_mode: false,
            allow_list_hash: String::new(),
            timeout_ms: 5000,
        };
        let result = watchdog.execute_action_real(req).await;
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert_eq!(err.code(), tonic::Code::PermissionDenied);
        assert!(err.message().contains("Skill not in registry"));
        let _ = fs::remove_dir_all(temp);
        std::env::remove_var("PAGI_DISABLE_QDRANT");
    }

    #[tokio::test]
    async fn test_execute_action_timeout() {
        let _g = lock_test_env();
        std::env::set_var("PAGI_DISABLE_QDRANT", "1");
        let temp = temp_bridge_dir(&["peek_file", "sleep"], true);
        let registry = temp.join("registry");
        fs::create_dir_all(&registry).unwrap();
        let memory = MemoryManager::new_async().await.unwrap();
        let core_dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        let watchdog = Watchdog::new(registry, memory, core_dir, temp.clone());
        let req = ActionRequest {
            skill_name: "sleep".to_string(),
            params: HashMap::new(),
            depth: 0,
            reasoning_id: "r1".to_string(),
            mock_mode: false,
            allow_list_hash: String::new(),
            timeout_ms: 50,
        };
        let result = watchdog.execute_action_real(req).await;
        assert!(result.is_ok());
        let resp = result.unwrap();
        assert!(!resp.success);
        assert!(resp.error.contains("Execution timed out"));
        let _ = fs::remove_dir_all(temp);
        std::env::remove_var("PAGI_DISABLE_QDRANT");
    }

    #[tokio::test]
    async fn test_apply_patch_auto_commit() {
        let _g = lock_test_env();
        // When PAGI_AUTO_COMMIT_SELF_PATCH=false, apply_patch succeeds but returns empty commit_hash (no git commit).
        std::env::set_var("PAGI_AUTO_COMMIT_SELF_PATCH", "false");
        let temp_registry = std::env::temp_dir().join(format!("pagi_apply_test_{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&temp_registry).unwrap();
        let _ = Repository::init(&temp_registry);
        std::env::set_var("PAGI_DISABLE_QDRANT", "true");
        std::env::set_var("PAGI_SKIP_APPLY_TEST", "true");
        let memory = MemoryManager::new_async().await.unwrap();
        let core_dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        let bridge_dir = std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join("../pagi-intelligence-bridge");
        let bridge_dir = if bridge_dir.exists() {
            bridge_dir.canonicalize().unwrap_or(bridge_dir)
        } else {
            core_dir.clone()
        };
        let watchdog = Watchdog::new(temp_registry.clone(), memory, core_dir, bridge_dir);
        let propose_resp = watchdog
            .propose_patch(PatchRequest {
                error_trace: "test apply_patch auto_commit".to_string(),
                component: "rust_core".to_string(),
            })
            .await
            .unwrap();
        let apply_resp = watchdog
            .apply_patch(ApplyRequest {
                patch_id: propose_resp.patch_id,
                approved: true,
                component: "rust_core".to_string(),
                requires_hitl: propose_resp.requires_hitl,
            })
            .await
            .unwrap();
        assert!(apply_resp.success);
        assert!(
            apply_resp.commit_hash.is_empty(),
            "commit_hash should be empty when PAGI_AUTO_COMMIT_SELF_PATCH=false"
        );
        let _ = fs::remove_dir_all(temp_registry);
        std::env::remove_var("PAGI_AUTO_COMMIT_SELF_PATCH");
        std::env::remove_var("PAGI_SKIP_APPLY_TEST");
        std::env::remove_var("PAGI_DISABLE_QDRANT");
    }

    #[tokio::test]
    async fn test_apply_patch_auto_commit_when_enabled() {
        let _g = lock_test_env();
        // When PAGI_AUTO_COMMIT_SELF_PATCH=true (default), apply_patch commits and returns non-empty commit_hash.
        std::env::set_var("PAGI_AUTO_COMMIT_SELF_PATCH", "true");
        let temp_registry = std::env::temp_dir().join(format!("pagi_apply_commit_{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&temp_registry).unwrap();
        let _ = Repository::init(&temp_registry);
        std::env::set_var("PAGI_DISABLE_QDRANT", "true");
        std::env::set_var("PAGI_SKIP_APPLY_TEST", "true");
        let memory = MemoryManager::new_async().await.unwrap();
        let core_dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        let bridge_dir = std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join("../pagi-intelligence-bridge");
        let bridge_dir = if bridge_dir.exists() {
            bridge_dir.canonicalize().unwrap_or(bridge_dir)
        } else {
            core_dir.clone()
        };
        let watchdog = Watchdog::new(temp_registry.clone(), memory, core_dir, bridge_dir);
        let propose_resp = watchdog
            .propose_patch(PatchRequest {
                error_trace: "test apply_patch auto_commit when enabled".to_string(),
                component: "rust_core".to_string(),
            })
            .await
            .unwrap();
        let apply_resp = watchdog
            .apply_patch(ApplyRequest {
                patch_id: propose_resp.patch_id,
                approved: true,
                component: "rust_core".to_string(),
                requires_hitl: propose_resp.requires_hitl,
            })
            .await
            .unwrap();
        assert!(apply_resp.success);
        assert!(
            !apply_resp.commit_hash.is_empty(),
            "commit_hash should be set when PAGI_AUTO_COMMIT_SELF_PATCH=true (git commit performed)"
        );
        let _ = fs::remove_dir_all(temp_registry);
        std::env::remove_var("PAGI_AUTO_COMMIT_SELF_PATCH");
        std::env::remove_var("PAGI_SKIP_APPLY_TEST");
        std::env::remove_var("PAGI_DISABLE_QDRANT");
    }

    fn temp_bridge_repo_for_auto_evolve() -> PathBuf {
        // Create a minimal bridge-like directory with:
        // - src/skills/evolve_skill_from_patch.py (for allow-list)
        // - scripts/run_skill.py (runner used by execute_action_real)
        // - initialized as a git repo so open_bridge_repo() works.
        //
        // Runner implements evolve_skill_from_patch: writes evolved_auto_evolve_test.py and returns EVOLVED_PATH:...
        let temp = std::env::temp_dir().join(format!(
            "pagi_watchdog_auto_evolve_{}",
            uuid::Uuid::new_v4()
        ));
        let skills_dir = temp.join("src").join("skills");
        let scripts_dir = temp.join("scripts");
        fs::create_dir_all(&skills_dir).unwrap();
        fs::create_dir_all(&scripts_dir).unwrap();

        // evolve_skill_from_patch.py for allow-list; runner implements it and returns EVOLVED_PATH:...
        fs::write(
            skills_dir.join("evolve_skill_from_patch.py"),
            "# fixture for allow-list\n",
        )
        .unwrap();

        fs::write(
            scripts_dir.join("run_skill.py"),
            r##"from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 3:
        print("[fixture run_skill] usage: python run_skill.py <skill_name> <json_params>", file=sys.stderr)
        raise SystemExit(1)

    skill_name = sys.argv[1]
    params = json.loads(sys.argv[2] or "{}")

    if skill_name == "evolve_skill_from_patch":
        rel = "src/skills/evolved_auto_evolve_test.py"
        skills_dir = Path(__file__).resolve().parent.parent / "src" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "evolved_auto_evolve_test.py").write_text("# auto-evolved stub\n", encoding="utf-8")
        print(f"EVOLVED_PATH:{rel}")
        return

    print(f"[fixture run_skill] unknown skill: {skill_name}", file=sys.stderr)
    raise SystemExit(4)


if __name__ == "__main__":
    main()
"##,
        )
        .unwrap();

        let _ = Repository::init(&temp).unwrap();

        temp
    }

    #[tokio::test]
    async fn test_apply_patch_auto_evolve() {
        // Mock successful apply/commit; PAGI_AUTO_EVOLVE_SKILLS=true.
        // Assert: evolve_skill_from_patch request is executed and bridge commit "Auto-evolved skill from self-patch" is called.
        let _g = lock_test_env();
        std::env::set_var("PAGI_AUTO_COMMIT_SELF_PATCH", "true");
        std::env::set_var("PAGI_AUTO_EVOLVE_SKILLS", "true");
        std::env::set_var("PAGI_DISABLE_QDRANT", "true");
        std::env::set_var("PAGI_SKIP_APPLY_TEST", "true");

        let temp_registry = std::env::temp_dir().join(format!(
            "pagi_apply_auto_evolve_registry_{}",
            uuid::Uuid::new_v4()
        ));
        fs::create_dir_all(&temp_registry).unwrap();
        let _ = Repository::init(&temp_registry);

        let temp_bridge = temp_bridge_repo_for_auto_evolve();

        let memory = MemoryManager::new_async().await.unwrap();
        let core_dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        let watchdog = Watchdog::new(temp_registry.clone(), memory, core_dir, temp_bridge.clone());

        let propose_resp = watchdog
            .propose_patch(PatchRequest {
                error_trace: "test auto evolve".to_string(),
                component: "python_skill".to_string(),
            })
            .await
            .unwrap();

        let apply_resp = watchdog
            .apply_patch(ApplyRequest {
                patch_id: propose_resp.patch_id,
                approved: true,
                component: "python_skill".to_string(),
                requires_hitl: propose_resp.requires_hitl,
            })
            .await
            .unwrap();

        assert!(apply_resp.success, "apply_patch should succeed");
        assert!(
            !apply_resp.commit_hash.is_empty(),
            "expected registry commit_hash when auto-commit enabled"
        );

        // Assert: evolve_skill_from_patch produced a skill file in bridge.
        let skill_path = temp_bridge
            .join("src")
            .join("skills")
            .join("evolved_auto_evolve_test.py");
        assert!(
            skill_path.exists(),
            "expected evolved skill file from evolve_skill_from_patch"
        );

        // Assert: bridge repo has commit "Auto-evolved skill from self-patch".
        let repo = Repository::open(&temp_bridge).unwrap();
        let head = repo.head().unwrap();
        let commit = head.peel_to_commit().unwrap();
        assert_eq!(
            commit.message().unwrap_or("").trim(),
            "Auto-evolved skill from self-patch",
            "expected bridge commit message after auto-evolve"
        );

        let _ = fs::remove_dir_all(temp_bridge);
        let _ = fs::remove_dir_all(temp_registry);

        std::env::remove_var("PAGI_AUTO_COMMIT_SELF_PATCH");
        std::env::remove_var("PAGI_AUTO_EVOLVE_SKILLS");
        std::env::remove_var("PAGI_SKIP_APPLY_TEST");
        std::env::remove_var("PAGI_DISABLE_QDRANT");
    }
}
