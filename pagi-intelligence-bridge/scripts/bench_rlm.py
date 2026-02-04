#!/usr/bin/env python3
"""Micro-benchmark for the Python RLM loop (no external dependencies).

This avoids pytest/poetry so it can run in constrained environments.

Runs a handful of representative loop paths:
- structured stub JSON parsing (no action)
- structured stub JSON + local dispatch action (peek_file)
- mock mode (no outbound)

Usage:
  python scripts/bench_rlm.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path


# Ensure `src/` is importable when running from `scripts/`.
_BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(_BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRIDGE_ROOT))


def _run_case(name: str, env: dict[str, str], iters: int) -> None:
    from src.recursive_loop import RLMQuery, recursive_loop

    old = dict(os.environ)
    try:
        os.environ.update(env)
        t0 = time.perf_counter()
        converged = 0
        for _ in range(iters):
            out = recursive_loop(RLMQuery(query="bench", context="resolved", depth=0))
            converged += 1 if out.converged else 0
        dt = time.perf_counter() - t0
        rps = iters / dt if dt > 0 else float("inf")
        print(f"{name:35s}  {rps:10.1f} it/s  converged={converged}/{iters}")
    finally:
        os.environ.clear()
        os.environ.update(old)


def main() -> None:
    iters = int(os.environ.get("PAGI_BENCH_ITERS", "2000"))

    # Case 1: Structured stub JSON -> final (parse + pydantic validation only)
    _run_case(
        "structured_stub_final",
        {
            "PAGI_MOCK_MODE": "false",
            "PAGI_RLM_STUB_JSON": '{"thought":"done","action":null,"observation":null,"is_final":true}',
            "PAGI_ENFORCE_STRUCTURED": "true",
            "PAGI_ALLOW_OUTBOUND": "false",
            "PAGI_ALLOW_LOCAL_DISPATCH": "false",
            "PAGI_ACTIONS_VIA_GRPC": "false",
            "PAGI_VERBOSE_ACTIONS": "false",
        },
        iters,
    )

    # Case 2: Structured stub JSON -> action (peek_file) executed via local dispatch
    with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8") as f:
        f.write("hello world")
        tmp_path = f.name

    _run_case(
        "structured_stub_local_dispatch_peek",
        {
            "PAGI_MOCK_MODE": "false",
            "PAGI_RLM_STUB_JSON": (
                '{'
                '"thought":"peek",'
                '"action":{"skill_name":"peek_file","params":{"path":"%s","start":0,"end":5}},'
                '"is_final":false'
                '}'
            )
            % tmp_path.replace("\\", "\\\\"),
            "PAGI_ENFORCE_STRUCTURED": "true",
            "PAGI_ALLOW_OUTBOUND": "false",
            "PAGI_ALLOW_LOCAL_DISPATCH": "true",
            "PAGI_ACTIONS_VIA_GRPC": "false",
            "PAGI_VERBOSE_ACTIONS": "false",
        },
        iters,
    )

    # Case 3: Mock mode
    _run_case(
        "mock_mode",
        {
            "PAGI_MOCK_MODE": "true",
            "PAGI_RLM_STUB_JSON": "",
            "PAGI_ENFORCE_STRUCTURED": "true",
            "PAGI_ALLOW_OUTBOUND": "false",
            "PAGI_ALLOW_LOCAL_DISPATCH": "false",
            "PAGI_ACTIONS_VIA_GRPC": "false",
            "PAGI_VERBOSE_ACTIONS": "false",
        },
        iters,
    )


if __name__ == "__main__":
    main()

