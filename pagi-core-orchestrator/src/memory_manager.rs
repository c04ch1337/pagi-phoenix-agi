// 7-Layer memory hierarchy. L4: semantic (Qdrant), 1536-dim cap, 8 KBs.
// L1/L2: DashMap stubs; L3/L5–L7: SurrealDB/other stubs deferred.

use std::sync::Arc;

use dashmap::DashMap;
use qdrant_client::prelude::*;
use qdrant_client::prelude::{Payload, PointStruct};
use qdrant_client::qdrant::{
    point_id::PointIdOptions, value::Kind, vectors_config, CreateCollection, Distance,
    PointId, SearchPoints, VectorParams, VectorsConfig,
};
use tonic::Status;

use crate::proto::pagi_proto::{
    SearchHit, SearchRequest, SearchResponse, UpsertRequest, UpsertResponse,
};

/// Tiered memory manager; layers 1–7 per blueprint.
pub struct MemoryManager {
    /// L1 sensory: ring-buffer stub (key -> raw bytes).
    l1_sensory: DashMap<String, Vec<u8>>,
    /// L2 working memory.
    l2_working: DashMap<String, String>,
    /// L4 semantic: local Qdrant client (1536-dim cap).
    l4_semantic: Option<QdrantClient>,
    /// Cached embedding dim to avoid env parsing on hot paths.
    embedding_dim: usize,
    /// Cached zero vector for fallback queries.
    zero_vector: Vec<f32>,
}

impl MemoryManager {
    fn embedding_dim_from_env() -> usize {
        std::env::var("PAGI_EMBEDDING_DIM")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(1536)
    }

    /// Create and connect to Qdrant at URI from PAGI_QDRANT_URI. Use init_kbs() after to create collections.
    pub async fn new_async() -> Result<Arc<Self>, Box<dyn std::error::Error + Send + Sync>> {
        let embedding_dim = Self::embedding_dim_from_env();
        let zero_vector = vec![0f32; embedding_dim];

        // Allow running orchestrator without Qdrant for Phase-3 loop/action testing.
        // This keeps polyglot wiring verifiable even when L4 infra is absent.
        if std::env::var("PAGI_DISABLE_QDRANT")
            .ok()
            .map(|v| matches!(v.to_lowercase().as_str(), "1" | "true" | "yes" | "on"))
            .unwrap_or(false)
        {
            return Ok(Arc::new(Self {
                l1_sensory: DashMap::new(),
                l2_working: DashMap::new(),
                l4_semantic: None,
                embedding_dim,
                zero_vector,
            }));
        }

        let uri = std::env::var("PAGI_QDRANT_URI").unwrap_or_else(|_| "http://localhost:6334".into());
        let mut config = QdrantClientConfig::from_url(&uri);
        if let Ok(key) = std::env::var("PAGI_QDRANT_API_KEY") {
            if !key.is_empty() {
                config.set_api_key(&key);
            }
        }
        let l4_semantic = QdrantClient::new(Some(config)).await?;
        Ok(Arc::new(Self {
            l1_sensory: DashMap::new(),
            l2_working: DashMap::new(),
            l4_semantic: Some(l4_semantic),
            embedding_dim,
            zero_vector,
        }))
    }

    /// Generic init for 8 KBs; dimensions from PAGI_EMBEDDING_DIM (default 1536), cosine distance.
    pub async fn init_kbs(&self) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let Some(l4) = self.l4_semantic.as_ref() else {
            // Qdrant disabled; L4 init is a no-op.
            return Ok(());
        };
        let dim = self.embedding_dim as u64;
        let kb_names = [
            "kb_core",
            "kb_skills",
            "kb_1",
            "kb_2",
            "kb_3",
            "kb_4",
            "kb_5",
            "kb_6",
        ];
        for name in kb_names {
            if l4.has_collection(name).await? {
                continue;
            }
            l4
                .create_collection(&CreateCollection {
                    collection_name: name.into(),
                    vectors_config: Some(VectorsConfig {
                        config: Some(vectors_config::Config::Params(VectorParams {
                            size: dim,
                            distance: Distance::Cosine.into(),
                        })),
                    }),
                    ..Default::default()
                })
                .await?;
        }
        Ok(())
    }

    /// Sync constructor for tests without Qdrant; L4 operations will fail.
    #[allow(dead_code)]
    pub fn new_stub() -> Arc<Self> {
        unimplemented!("Use new_async() for production; stub only for unit tests without Qdrant")
    }

    /// Access memory by layer (1–7), key, and optional value for writes.
    pub fn access(&self, layer: i32, key: &str, value: Option<&str>) -> (String, bool) {
        match layer {
            1 => {
                if let Some(v) = value {
                    self.l1_sensory.insert(key.to_string(), v.as_bytes().to_vec());
                }
                (
                    self.l1_sensory
                        .get(key)
                        .map(|g| String::from_utf8_lossy(g.value()).into_owned())
                        .unwrap_or_default(),
                    true,
                )
            }
            2 => {
                if let Some(v) = value {
                    self.l2_working.insert(key.to_string(), v.to_string());
                }
                (
                    self.l2_working
                        .get(key)
                        .map(|g| g.value().clone())
                        .unwrap_or_default(),
                    true,
                )
            }
            _ => (String::new(), true),
        }
    }

    /// L4 semantic search. Uses query_vector when provided (Python embed); else zero vector (stub).
    /// When Qdrant is disabled, returns empty hits so callers (e.g. propose_patch) can still run.
    pub async fn semantic_search(
        &self,
        req: SearchRequest,
    ) -> Result<SearchResponse, Status> {
        let Some(l4) = self.l4_semantic.as_ref() else {
            return Ok(SearchResponse { hits: vec![] });
        };
        let limit = req.limit.max(1).min(100) as u64;
        let dim = self.embedding_dim;
        let query_vector: Vec<f32> = if req.query_vector.len() == dim {
            req.query_vector
        } else {
            self.zero_vector.clone()
        };

        let search_req = SearchPoints {
            collection_name: req.kb_name.clone(),
            vector: query_vector,
            filter: None,
            limit,
            with_payload: Some(true.into()),
            params: None,
            score_threshold: None,
            offset: None,
            vector_name: None,
            with_vectors: None,
        };

        let response = l4
            .search_points(&search_req)
            .await
            .map_err(|e| Status::internal(e.to_string()))?;

        let hits: Vec<SearchHit> = response
            .result
            .into_iter()
            .map(|p| {
                let document_id = p
                    .id
                    .and_then(|id| id.point_id_options)
                    .map(|opt| match opt {
                        PointIdOptions::Num(n) => n.to_string(),
                        PointIdOptions::Uuid(s) => s,
                    })
                    .unwrap_or_else(String::new);
                let content_snippet = p
                    .payload
                    .get("content")
                    .or_else(|| p.payload.get("snippet"))
                    .and_then(|v| {
                        if let Some(Kind::StringValue(s)) = v.kind.as_ref() {
                            Some(s.clone())
                        } else {
                            None
                        }
                    })
                    .unwrap_or_else(|| "Snippet stub".to_string());
                SearchHit {
                    document_id,
                    score: p.score,
                    content_snippet,
                }
            })
            .collect();

        Ok(SearchResponse { hits })
    }

    /// L4 upsert: store vector points into a KB collection. Python embeds; Rust owns I/O.
    pub async fn upsert_vectors(&self, req: UpsertRequest) -> Result<UpsertResponse, Status> {
        let l4 = self
            .l4_semantic
            .as_ref()
            .ok_or_else(|| Status::failed_precondition("Qdrant disabled (PAGI_DISABLE_QDRANT=true)"))?;

        let mut points: Vec<PointStruct> = Vec::with_capacity(req.points.len());
        for p in req.points {
            let mut payload = Payload::new();
            for (k, v) in p.payload {
                payload.insert(k, v);
            }
            points.push(PointStruct::new(PointId::from(p.id), p.vector, payload));
        }
        let n = points.len();
        l4
            .upsert_points_blocking(&req.kb_name, points)
            .await
            .map_err(|e| Status::internal(e.to_string()))?;
        Ok(UpsertResponse {
            success: true,
            upserted_count: n as u32,
        })
    }
}
