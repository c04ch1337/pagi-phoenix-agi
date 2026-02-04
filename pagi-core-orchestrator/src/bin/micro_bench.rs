//! Simple micro-benchmark for hot paths that do not require external services.
//!
//! Usage:
//!   PAGI_DISABLE_QDRANT=true cargo run --release --bin micro_bench

use std::time::Instant;

// This binary is a separate crate target; re-use the production module directly.
#[path = "../proto.rs"]
mod proto;

#[path = "../memory_manager.rs"]
mod memory_manager;

use memory_manager::MemoryManager;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    // Ensure we don't attempt to talk to Qdrant.
    std::env::set_var("PAGI_DISABLE_QDRANT", "true");

    let mm = MemoryManager::new_async().await?;

    let iters: usize = std::env::var("PAGI_BENCH_ITERS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(2_000_000);

    // L2 access (string clone path)
    let t0 = Instant::now();
    for i in 0..iters {
        let _ = mm.access(2, "k", Some(if (i & 1) == 0 { "a" } else { "b" }));
        let _ = mm.access(2, "k", None);
    }
    let dt = t0.elapsed().as_secs_f64();
    let ops = (iters as f64) * 2.0;
    eprintln!("L2 access: {:>10.0} ops/s", ops / dt);

    // L1 access (bytes conversion path)
    let t1 = Instant::now();
    for i in 0..iters {
        let _ = mm.access(1, "k", Some(if (i & 1) == 0 { "a" } else { "b" }));
        let _ = mm.access(1, "k", None);
    }
    let dt = t1.elapsed().as_secs_f64();
    let ops = (iters as f64) * 2.0;
    eprintln!("L1 access: {:>10.0} ops/s", ops / dt);

    Ok(())
}

