#!/usr/bin/env python3
"""Validate pagi.proto compilation: generate Python stubs and peek message/service definitions.

Run from repo root: poetry run python scripts/peek_proto.py
Or from pagi-intelligence-bridge: poetry run python scripts/peek_proto.py
"""

from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    proto_dir = repo_root / "pagi-proto"
    proto_file = proto_dir / "pagi.proto"
    out_dir = repo_root / "pagi-intelligence-bridge" / "src" / "pagi_pb"

    if not proto_file.exists():
        raise FileNotFoundError(f"Proto not found: {proto_file}")

    # Compile with grpcio_tools (must be installed: poetry add --group dev grpcio-tools)
    try:
        import grpc_tools.protoc as protoc
    except ImportError:
        print("Install grpcio-tools: poetry add --group dev grpcio-tools")
        raise

    out_dir.mkdir(parents=True, exist_ok=True)

    args = [
        f"-I{proto_dir}",
        f"--python_out={out_dir}",
        f"--grpc_python_out={out_dir}",
        str(proto_file),
    ]
    protoc.main(args)

    # Peek: list generated modules and key types
    pb2_file = out_dir / "pagi_pb2.py"
    pb2_grpc_file = out_dir / "pagi_pb2_grpc.py"
    if not pb2_file.exists():
        raise RuntimeError("Expected pagi_pb2.py after compile; check grpc_tools output.")

    print("Proto compilation OK.")
    print(f"  Generated: {pb2_file}")
    print(f"  Generated: {pb2_grpc_file}")

    # Import and list message/descriptor names
    import importlib.util
    spec = importlib.util.spec_from_file_location("pagi_pb2", pb2_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    messages = [name for name in dir(mod) if not name.startswith("_")]
    print("  Messages:", ", ".join(messages))


if __name__ == "__main__":
    main()
