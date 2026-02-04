#!/usr/bin/env python3
"""Embed documents with Sentence Transformers and upsert to L4 via gRPC (MemoryManager).

Bootstrap kb_core with generic docs (e.g. ARCHITECTURE.md, README.md). Chunks < 10k chars;
vectors padded to PAGI_EMBEDDING_DIM (default 1536) for collection compatibility.
Search mode: embed query → SemanticSearch with query_vector for end-to-end L4 verification.
Usage:
  poetry run python src/embed_and_upsert.py --doc path/to/doc.md --kb kb_core
  poetry run python src/embed_and_upsert.py --search "hierarchy" --kb kb_core --limit 5
"""

import argparse
import os
import sys
from pathlib import Path

# Generated stubs live in pagi_pb/ and grpc file does "import pagi_pb2"; add pagi_pb dir to path.
_pagi_pb_dir = Path(__file__).resolve().parent / "pagi_pb"
if str(_pagi_pb_dir) not in sys.path:
    sys.path.insert(0, str(_pagi_pb_dir))

import pagi_pb2
import pagi_pb2_grpc


def _embedding_dim() -> int:
    return int(os.environ.get("PAGI_EMBEDDING_DIM", "1536"))


def _grpc_addr() -> str:
    port = os.environ.get("PAGI_GRPC_PORT", "50051")
    return f"[::1]:{port}"


def embed_text(text: str, model) -> list[float]:
    vec = model.encode(text).tolist()
    dim = _embedding_dim()
    if len(vec) < dim:
        vec = vec + [0.0] * (dim - len(vec))
    elif len(vec) > dim:
        vec = vec[:dim]
    return vec


def search_kb(
    query: str,
    kb_name: str = "kb_core",
    limit: int = 5,
    grpc_addr: str | None = None,
    model_name: str | None = None,
):
    """Embed query, call SemanticSearch with query_vector, return hits (for L4 demo)."""
    import grpc
    from sentence_transformers import SentenceTransformer

    grpc_addr = grpc_addr or _grpc_addr()
    model_name = model_name or os.environ.get("PAGI_EMBED_MODEL", "all-MiniLM-L6-v2")
    model = SentenceTransformer(model_name)
    vector = embed_text(query, model)

    channel = grpc.insecure_channel(grpc_addr)
    stub = pagi_pb2_grpc.PagiStub(channel)
    req = pagi_pb2.SearchRequest(
        query=query,
        kb_name=kb_name,
        limit=min(max(limit, 1), 100),
        query_vector=vector,
    )
    response = stub.SemanticSearch(req)
    return response.hits


def chunk_doc(file_path: str | Path, chunk_size: int = 1000) -> list[str]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Doc not found: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    return [
        text[i : i + chunk_size]
        for i in range(0, len(text), chunk_size)
    ]


def upsert_to_kb(
    kb_name: str,
    doc_path: str | Path,
    grpc_addr: str | None = None,
    chunk_size: int = 1000,
    model_name: str | None = None,
):
    import grpc
    from sentence_transformers import SentenceTransformer

    grpc_addr = grpc_addr or _grpc_addr()
    model_name = model_name or os.environ.get("PAGI_EMBED_MODEL", "all-MiniLM-L6-v2")
    model = SentenceTransformer(model_name)

    channel = grpc.insecure_channel(grpc_addr)
    stub = pagi_pb2_grpc.PagiStub(channel)
    chunks = chunk_doc(doc_path, chunk_size=chunk_size)
    doc_basename = Path(doc_path).name

    points = []
    for idx, chunk in enumerate(chunks):
        vector = embed_text(chunk, model)
        snippet = (chunk[:500] + "…") if len(chunk) > 500 else chunk
        point = pagi_pb2.VectorPoint(
            id=f"{doc_basename}_chunk_{idx}",
            vector=vector,
            payload={"content": snippet},
        )
        points.append(point)

    req = pagi_pb2.UpsertRequest(kb_name=kb_name, points=points)
    response = stub.UpsertVectors(req)
    return response


def main() -> None:
    import grpc

    parser = argparse.ArgumentParser(description="Embed doc and upsert to L4 KB, or search with embedded query")
    parser.add_argument("--doc", help="Path to document to index (e.g. ARCHITECTURE.md)")
    parser.add_argument("--search", help="Query string: embed and run SemanticSearch (demo L4 end-to-end)")
    parser.add_argument("--kb", default="kb_core", help="KB collection name")
    parser.add_argument("--limit", type=int, default=5, help="Max search results (with --search)")
    parser.add_argument("--grpc", default=None, help="gRPC address (default [::1]:PAGI_GRPC_PORT)")
    parser.add_argument("--chunk-size", type=int, default=1000, help="Chars per chunk (indexing only)")
    args = parser.parse_args()

    if args.search:
        try:
            hits = search_kb(args.search, kb_name=args.kb, limit=args.limit, grpc_addr=args.grpc)
            print(f"Query: \"{args.search}\" -> {len(hits)} hit(s) in {args.kb}")
            for i, h in enumerate(hits, 1):
                print(f"  [{i}] id={h.document_id} score={h.score:.4f}")
                snip = h.content_snippet[:200] + ("..." if len(h.content_snippet) > 200 else "")
                print(f"      snippet: {snip}")
        except grpc.RpcError as e:
            print(f"gRPC error: {e.code()} {e.details()}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if not args.doc:
        parser.error("Either --doc or --search is required")
    try:
        resp = upsert_to_kb(
            args.kb,
            args.doc,
            grpc_addr=args.grpc,
            chunk_size=args.chunk_size,
        )
        print(f"Upserted {resp.upserted_count} points to {args.kb} (success={resp.success})")
        # L6 traceability: log KB bootstrap when audit log is configured
        log_path = os.environ.get("PAGI_SELF_HEAL_LOG")
        if log_path and resp.success:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"L6 KB bootstrap: indexed {args.doc} -> {args.kb} ({resp.upserted_count} points)\n")
    except grpc.RpcError as e:
        print(f"gRPC error: {e.code()} {e.details()}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
