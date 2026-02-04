"""Generated gRPC stubs from `pagi.proto`.

grpcio-tools generates absolute imports inside `pagi_pb2_grpc.py` (e.g. `import pagi_pb2`).
When these files live inside a package (this directory), those absolute imports can fail.

To keep regeneration simple and avoid patching generated files, we install import aliases
into `sys.modules` so `pagi_pb2_grpc.py` can resolve `pagi_pb2` reliably.
"""

from __future__ import annotations

import sys

# Alias `pagi_pb2` before importing `pagi_pb2_grpc` (which imports it by absolute name).
from . import pagi_pb2 as _pagi_pb2

sys.modules.setdefault("pagi_pb2", _pagi_pb2)

from . import pagi_pb2_grpc as _pagi_pb2_grpc

sys.modules.setdefault("pagi_pb2_grpc", _pagi_pb2_grpc)

__all__ = [
    "_pagi_pb2",
    "_pagi_pb2_grpc",
]
