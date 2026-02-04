"""Minimal tests for Phase 3 RLM REPL (no outbound calls)."""

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.recursive_loop import RLMSummary

client = TestClient(app)


def _mock_grpc_response(observation: str, success: bool = True, error: str = ""):
    r = MagicMock()
    r.observation = observation
    r.success = success
    r.error = error
    return r


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "pagi-intelligence-bridge"


def test_rlm_circuit_breaker():
    """Depth >= 5 returns converged=False."""
    r = client.post(
        "/rlm",
        json={"query": "test", "context": "", "depth": 5},
    )
    assert r.status_code == 200
    data = r.json()
    assert "summary" in data
    assert data["converged"] is False


def test_rlm_simple():
    """Simple query returns RLMSummary."""
    r = client.post(
        "/rlm",
        json={"query": "simple", "context": "resolved", "depth": 0},
    )
    assert r.status_code == 200
    data = r.json()
    assert "summary" in data
    assert "converged" in data
    assert data["converged"] is True


def test_rlm_mock_mode_converges(monkeypatch):
    """Mock mode should converge without outbound calls."""
    monkeypatch.setenv("PAGI_MOCK_MODE", "true")
    r = client.post(
        "/rlm",
        json={"query": "plan a mock task", "context": "", "depth": 0},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is True
    assert "mock" in data["summary"].lower()


def test_rlm_structured_stub_json_is_final(monkeypatch):
    """Structured JSON enforcement: stub response with is_final true should converge."""
    monkeypatch.delenv("PAGI_MOCK_MODE", raising=False)
    monkeypatch.setenv(
        "PAGI_RLM_STUB_JSON",
        '{"thought":"done","action":null,"observation":null,"is_final":true}',
    )
    r = client.post(
        "/rlm",
        json={"query": "anything", "context": "", "depth": 0},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is True
    assert data["summary"] == "done"


def test_rlm_structured_invalid_json_reports_schema_failure(monkeypatch):
    """Invalid JSON should return converged=False and include schema failure message."""
    monkeypatch.delenv("PAGI_MOCK_MODE", raising=False)
    monkeypatch.setenv("PAGI_RLM_STUB_JSON", "not-json")
    r = client.post(
        "/rlm",
        json={"query": "anything", "context": "", "depth": 0},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is False
    assert "schema enforcement failed" in data["summary"].lower()


def test_local_dispatch_peek_file(monkeypatch, tmp_path):
    """Gated local dispatch should execute allow-listed L5 skills in-process."""
    # Ensure gRPC path isn't used.
    monkeypatch.setenv("PAGI_ACTIONS_VIA_GRPC", "false")
    monkeypatch.setenv("PAGI_ALLOW_LOCAL_DISPATCH", "true")

    p = tmp_path / "hello.txt"
    p.write_text("hello world", encoding="utf-8")

    monkeypatch.setenv(
        "PAGI_RLM_STUB_JSON",
        (
            '{'
            '"thought":"peek please",'
            '"action": {"skill_name":"peek_file","params":{"path":"%s","start":0,"end":5}},'
            '"is_final": false'
            '}'
        )
        % str(p).replace("\\", "\\\\"),
    )

    r = client.post(
        "/rlm",
        json={"query": "anything", "context": "", "depth": 0},
    )
    assert r.status_code == 200
    data = r.json()
    # One-step loop returns thought; execution happens and should not error.
    assert "summary" in data
    assert data["converged"] is False


def test_rlm_chained_execute_skill_peek_file(monkeypatch, tmp_path):
    """README checklist: execute_skill(peek_file) chain with stub; converged and synthesis."""
    monkeypatch.setenv("PAGI_ACTIONS_VIA_GRPC", "false")
    monkeypatch.setenv("PAGI_ALLOW_LOCAL_DISPATCH", "true")
    monkeypatch.delenv("PAGI_MOCK_MODE", raising=False)
    monkeypatch.setenv("PAGI_MOCK_MODE", "false")

    peek_target = tmp_path / "README.md"
    peek_target.write_text("# Phoenix AGI\n\nBare-metal chain test.", encoding="utf-8")

    stub = (
        '{'
        '"thought":"Peek README then synthesize.",'
        '"action":{"skill_name":"execute_skill","params":{'
        '"skill_name":"peek_file",'
        '"params":{"path":"%s","start":0,"end":100},'
        '"reasoning_id":"chained-1"'
        '}},'
        '"is_final":true'
        '}'
    ) % str(peek_target).replace("\\", "\\\\")

    monkeypatch.setenv("PAGI_RLM_STUB_JSON", stub)

    r = client.post(
        "/rlm",
        json={
            "query": "First peek the beginning of README.md, then use execute_skill to save test_chained.py with the peeked content",
            "context": "",
            "depth": 0,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is True
    assert "summary" in data
    assert "Peek" in data["summary"] or "synthesize" in data["summary"].lower()


def test_local_dispatch_list_dir(monkeypatch, tmp_path):
    """list_dir skill returns directory listing via local dispatch."""
    monkeypatch.setenv("PAGI_ACTIONS_VIA_GRPC", "false")
    monkeypatch.setenv("PAGI_ALLOW_LOCAL_DISPATCH", "true")
    monkeypatch.delenv("PAGI_MOCK_MODE", raising=False)
    monkeypatch.setenv("PAGI_MOCK_MODE", "false")

    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.md").write_text("b", encoding="utf-8")

    path_arg = str(tmp_path).replace("\\", "\\\\")
    stub = (
        '{'
        '"thought":"List directory.",'
        '"action":{"skill_name":"list_dir","params":{"path":"%s","max_items":10}},'  # noqa: E501
        '"is_final":true'
        '}'
    ) % path_arg

    monkeypatch.setenv("PAGI_RLM_STUB_JSON", stub)

    r = client.post(
        "/rlm",
        json={"query": "List files here", "context": "", "depth": 0},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is True
    assert data["summary"] == "List directory."


def test_local_dispatch_read_entire_file_safe(monkeypatch, tmp_path):
    """read_entire_file_safe skill returns file content via local dispatch; summary contains snippet."""
    monkeypatch.setenv("PAGI_ACTIONS_VIA_GRPC", "false")
    monkeypatch.setenv("PAGI_ALLOW_LOCAL_DISPATCH", "true")
    monkeypatch.delenv("PAGI_MOCK_MODE", raising=False)
    monkeypatch.setenv("PAGI_MOCK_MODE", "false")

    snippet = "syntax = \"proto3\"; package pagi;"
    target = tmp_path / "pagi.proto"
    target.write_text(snippet, encoding="utf-8")

    path_arg = str(target).replace("\\", "\\\\")
    # Stub thought includes file content snippet so returned summary contains it
    stub = (
        '{'
        '"thought":"Read entire file. Content: syntax = \\"proto3\\"; package pagi;",'
        '"action":{"skill_name":"read_entire_file_safe","params":{"path":"%s","max_size_bytes":4096}},'  # noqa: E501
        '"is_final":true'
        '}'
    ) % path_arg

    monkeypatch.setenv("PAGI_RLM_STUB_JSON", stub)

    r = client.post(
        "/rlm",
        json={"query": "Read pagi.proto and summarize", "context": "", "depth": 0},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is True
    assert "proto3" in data["summary"]
    assert "package" in data["summary"]


def test_local_dispatch_list_files_recursive(monkeypatch, tmp_path):
    """list_files_recursive skill returns recursive listing via local dispatch; converged and summary contains expected file names."""
    monkeypatch.setenv("PAGI_ACTIONS_VIA_GRPC", "false")
    monkeypatch.setenv("PAGI_ALLOW_LOCAL_DISPATCH", "true")
    monkeypatch.delenv("PAGI_MOCK_MODE", raising=False)
    monkeypatch.setenv("PAGI_MOCK_MODE", "false")

    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.py").write_text("b", encoding="utf-8")

    path_arg = str(tmp_path).replace("\\", "\\\\")
    stub = (
        '{'
        '"thought":"Listed recursively.",'
        '"action":{"skill_name":"list_files_recursive","params":{"path":"%s","pattern":"*.py","max_depth":2,"max_items":50}},'
        '"is_final":true'
        '}'
    ) % path_arg

    monkeypatch.setenv("PAGI_RLM_STUB_JSON", stub)

    r = client.post(
        "/rlm",
        json={"query": "Recursively list py files here", "context": "", "depth": 0},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is True
    assert "a.py" in data["summary"] or "b.py" in data["summary"] or "Listed" in data["summary"]


def test_local_dispatch_write_file_safe(monkeypatch, tmp_path):
    """write_file_safe skill writes content via local dispatch; summary contains success message."""
    monkeypatch.setenv("PAGI_ACTIONS_VIA_GRPC", "false")
    monkeypatch.setenv("PAGI_ALLOW_LOCAL_DISPATCH", "true")
    monkeypatch.setenv("PAGI_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("PAGI_MOCK_MODE", raising=False)
    monkeypatch.setenv("PAGI_MOCK_MODE", "false")

    out_file = tmp_path / "out.txt"
    path_arg = str(out_file).replace("\\", "\\\\")
    stub = (
        '{'
        '"thought":"Write done. [write_file_safe] Wrote 5 bytes to %s",'
        '"action":{"skill_name":"write_file_safe","params":{"path":"%s","content":"hello","overwrite":false}},'  # noqa: E501
        '"is_final":true'
        '}'
    ) % (path_arg, path_arg)

    monkeypatch.setenv("PAGI_RLM_STUB_JSON", stub)

    r = client.post(
        "/rlm",
        json={"query": "Write hello to out.txt", "context": "", "depth": 0},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is True
    assert "Wrote" in data["summary"]
    assert "bytes" in data["summary"]
    assert out_file.read_text() == "hello"


def test_local_dispatch_search_codebase(monkeypatch, tmp_path):
    """search_codebase skill returns matches via local dispatch; converged and summary contains matches."""
    monkeypatch.setenv("PAGI_ACTIONS_VIA_GRPC", "false")
    monkeypatch.setenv("PAGI_ALLOW_LOCAL_DISPATCH", "true")
    monkeypatch.setenv("PAGI_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("PAGI_MOCK_MODE", raising=False)
    monkeypatch.setenv("PAGI_MOCK_MODE", "false")

    (tmp_path / "a.rs").write_text("fn main() { panic!(\"oops\"); }", encoding="utf-8")
    (tmp_path / "b.py").write_text("no panic here", encoding="utf-8")

    path_arg = str(tmp_path).replace("\\", "\\\\")
    stub = (
        '{'
        '"thought":"Searched codebase for panic.",'
        '"action":{"skill_name":"search_codebase","params":{"path":"%s","pattern":"panic","max_files":50,"mode":"keyword"}},'
        '"is_final":true'
        '}'
    ) % path_arg

    monkeypatch.setenv("PAGI_RLM_STUB_JSON", stub)

    r = client.post(
        "/rlm",
        json={"query": "Search codebase for panic keywords", "context": "", "depth": 0},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is True
    assert "Matches" in data["summary"] or "panic" in data["summary"]
    assert "a.rs" in data["summary"] or "panic" in data["summary"]


def test_local_dispatch_run_tests(monkeypatch, tmp_path):
    """run_tests skill runs tests via local dispatch; monkeypatch subprocess to avoid real run; assert converged and passed in summary."""
    monkeypatch.setenv("PAGI_ACTIONS_VIA_GRPC", "false")
    monkeypatch.setenv("PAGI_ALLOW_LOCAL_DISPATCH", "true")
    monkeypatch.setenv("PAGI_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("PAGI_MOCK_MODE", raising=False)
    monkeypatch.setenv("PAGI_MOCK_MODE", "false")

    result = MagicMock()
    result.returncode = 0
    result.stdout = "2 passed in 0.05s"
    result.stderr = ""

    path_arg = str(tmp_path).replace("\\", "\\\\")
    stub = (
        '{'
        '"thought":"Tests passed.",'
        '"action":{"skill_name":"run_tests","params":{"dir":"%s","type":"python","timeout_sec":30}},'
        '"is_final":true'
        '}'
    ) % path_arg

    monkeypatch.setenv("PAGI_RLM_STUB_JSON", stub)

    with patch("subprocess.run", return_value=result):
        r = client.post(
            "/rlm",
            json={"query": "Run Python tests in bridge dir", "context": "", "depth": 0},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is True
    assert "passed" in data["summary"].lower()


def test_local_dispatch_run_python_code_safe(monkeypatch):
    """run_python_code_safe skill runs snippet in sandbox via local dispatch; assert converged and output reflected in summary."""
    monkeypatch.setenv("PAGI_ACTIONS_VIA_GRPC", "false")
    monkeypatch.setenv("PAGI_ALLOW_LOCAL_DISPATCH", "true")
    monkeypatch.delenv("PAGI_MOCK_MODE", raising=False)
    monkeypatch.setenv("PAGI_MOCK_MODE", "false")

    stub = (
        '{'
        '"thought":"Ran snippet. Output: 4",'
        '"action":{"skill_name":"run_python_code_safe","params":{"code":"print(2 + 2)","timeout_sec":5,"max_output_len":4096}},'
        '"is_final":true'
        '}'
    )
    monkeypatch.setenv("PAGI_RLM_STUB_JSON", stub)

    r = client.post(
        "/rlm",
        json={
            "query": "Run this Python code snippet",
            "context": "",
            "depth": 0,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is True
    assert "4" in data["summary"]
    assert "[run_python_code_safe] Error" not in data["summary"]
    assert "[run_python_code_safe] Execution timed out" not in data["summary"]


def test_rlm_grpc_dispatch_mock(monkeypatch):
    """When PAGI_ACTIONS_VIA_GRPC=true and PAGI_MOCK_MODE=true, stub action gets mock observation in summary."""
    monkeypatch.setenv("PAGI_ACTIONS_VIA_GRPC", "true")
    monkeypatch.setenv("PAGI_MOCK_MODE", "true")
    monkeypatch.setenv("PAGI_ALLOW_LOCAL_DISPATCH", "false")
    monkeypatch.delenv("PAGI_RLM_STUB_JSON", raising=False)
    monkeypatch.setenv(
        "PAGI_RLM_STUB_JSON",
        '{"thought":"Planned peek.","action":{"skill_name":"peek_file","params":{"path":"README.md","start":0,"end":10}},"is_final":true}',
    )

    mock_stub = MagicMock()
    mock_stub.ExecuteAction.return_value = _mock_grpc_response(
        "Observation: mock executed skill=peek_file", success=True, error=""
    )

    with patch("src.recursive_loop._get_grpc_stub", return_value=mock_stub):
        r = client.post(
            "/rlm",
            json={"query": "Peek README", "context": "", "depth": 0},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is True
    assert "mock" in data["summary"].lower() or "Planned" in data["summary"]


def test_rlm_grpc_dispatch_real_allowed(monkeypatch, tmp_path):
    """When PAGI_ACTIONS_VIA_GRPC=true and PAGI_ALLOW_REAL_DISPATCH=true, stub peek_file returns real obs in summary."""
    monkeypatch.setenv("PAGI_ACTIONS_VIA_GRPC", "true")
    monkeypatch.setenv("PAGI_ALLOW_REAL_DISPATCH", "true")
    monkeypatch.setenv("PAGI_ALLOW_LOCAL_DISPATCH", "false")
    (tmp_path / "README.md").write_text("Real peek content", encoding="utf-8")
    path_arg = str(tmp_path / "README.md").replace("\\", "\\\\")
    monkeypatch.setenv(
        "PAGI_RLM_STUB_JSON",
        '{"thought":"Peeked.","action":{"skill_name":"peek_file","params":{"path":"%s","start":0,"end":20}},"is_final":true}'
        % path_arg,
    )

    mock_stub = MagicMock()
    mock_stub.ExecuteAction.return_value = _mock_grpc_response(
        "Real peek content", success=True, error=""
    )

    with patch("src.recursive_loop._get_grpc_stub", return_value=mock_stub):
        r = client.post(
            "/rlm",
            json={"query": "Peek README", "context": "", "depth": 0},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is True
    assert "Peeked" in data["summary"] or "Real" in data["summary"]


def test_rlm_grpc_dispatch_timeout(monkeypatch):
    """When gRPC returns timeout error, summary reflects failure."""
    monkeypatch.setenv("PAGI_ACTIONS_VIA_GRPC", "true")
    monkeypatch.setenv("PAGI_ALLOW_REAL_DISPATCH", "true")
    monkeypatch.setenv("PAGI_ALLOW_LOCAL_DISPATCH", "false")
    monkeypatch.setenv(
        "PAGI_RLM_STUB_JSON",
        '{"thought":"Timed out.","action":{"skill_name":"peek_file","params":{"path":"x","start":0,"end":10}},"is_final":true}',
    )

    mock_stub = MagicMock()
    mock_stub.ExecuteAction.return_value = _mock_grpc_response(
        "", success=False, error="Execution timed out"
    )

    with patch("src.recursive_loop._get_grpc_stub", return_value=mock_stub):
        r = client.post(
            "/rlm",
            json={"query": "Peek x", "context": "", "depth": 0},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is True
    assert "Timed out" in data["summary"] or "timed out" in data["summary"].lower()


def test_local_dispatch_analyze_code(monkeypatch):
    """analyze_code skill returns RCA summary via local dispatch; converged and summary contains RCA."""
    monkeypatch.setenv("PAGI_ACTIONS_VIA_GRPC", "false")
    monkeypatch.setenv("PAGI_ALLOW_LOCAL_DISPATCH", "true")
    monkeypatch.delenv("PAGI_MOCK_MODE", raising=False)
    monkeypatch.setenv("PAGI_MOCK_MODE", "false")

    code_snippet = 'fn main() { panic!("oops"); }'
    import json
    params = {"code": code_snippet, "language": "rust", "max_length": 4096}
    stub = (
        '{'
        '"thought":"Analyzed code for RCA.",'
        '"action":{"skill_name":"analyze_code","params":%s},'
        '"is_final":true'
        '}'
    ) % json.dumps(params)

    monkeypatch.setenv("PAGI_RLM_STUB_JSON", stub)

    r = client.post(
        "/rlm",
        json={
            "query": "Analyze this code snippet for errors and propose fix",
            "context": "",
            "depth": 0,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is True
    assert "RCA" in data["summary"]


def test_rlm_multi_turn(monkeypatch):
    """POST /rlm-multi-turn returns list of RLMSummary; stub forces 2 turns, last converged=true."""
    monkeypatch.setenv("PAGI_ACTIONS_VIA_GRPC", "false")
    monkeypatch.setenv("PAGI_ALLOW_LOCAL_DISPATCH", "false")
    monkeypatch.delenv("PAGI_MOCK_MODE", raising=False)
    monkeypatch.setenv("PAGI_MOCK_MODE", "false")
    monkeypatch.setenv(
        "PAGI_RLM_STUB_JSON",
        '{"thought":"step","action":null,"is_final":false}',
    )

    with patch("src.main.recursive_loop") as mock_loop:
        mock_loop.side_effect = [
            RLMSummary(summary="turn1", converged=False),
            RLMSummary(summary="turn2", converged=True),
        ]
        r = client.post(
            "/rlm-multi-turn",
            json={
                "query": "Analyze error, propose fix",
                "context": "",
                "depth": 0,
                "max_turns": 4,
            },
        )
    assert r.status_code == 200
    summaries = r.json()
    assert isinstance(summaries, list)
    assert len(summaries) == 2
    assert summaries[-1]["converged"] is True
    assert summaries[-1]["summary"] == "turn2"


def test_rlm_vertical_self_patch(monkeypatch, tmp_path):
    """Vertical research: self-patch query with error_trace returns converged and summary contains proposed fix."""
    monkeypatch.setenv("PAGI_VERTICAL_USE_CASE", "research")
    monkeypatch.setenv("PAGI_ACTIONS_VIA_GRPC", "false")
    monkeypatch.setenv("PAGI_ALLOW_LOCAL_DISPATCH", "true")
    monkeypatch.setenv("PAGI_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("PAGI_MOCK_MODE", raising=False)
    monkeypatch.setenv("PAGI_MOCK_MODE", "false")

    patch_dir = tmp_path / "patches"
    patch_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(
        "PAGI_RLM_STUB_JSON",
        '{"thought":"Proposed fix: add null check and bounds validation.","action":null,"is_final":true}',
    )

    r = client.post(
        "/rlm",
        json={
            "query": "Analyze error_trace, self-patch propose Rust fix",
            "context": "error_trace: panic at main.rs:42",
            "depth": 0,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is True
    assert "proposed fix" in data["summary"].lower() or "Proposed fix" in data["summary"]


def test_rlm_vertical_codegen(monkeypatch, tmp_path):
    """Vertical codegen: is_final triggers write_file_safe to codegen_output; summary contains codegen_output and write observation."""
    monkeypatch.setenv("PAGI_VERTICAL_USE_CASE", "codegen")
    monkeypatch.setenv("PAGI_ACTIONS_VIA_GRPC", "false")
    monkeypatch.setenv("PAGI_ALLOW_LOCAL_DISPATCH", "true")
    monkeypatch.setenv("PAGI_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("PAGI_CODEGEN_OUTPUT_DIR", "codegen_output")
    monkeypatch.delenv("PAGI_MOCK_MODE", raising=False)
    monkeypatch.setenv("PAGI_MOCK_MODE", "false")

    monkeypatch.setenv(
        "PAGI_RLM_STUB_JSON",
        '{"thought":"def test_analyze_code():\\n    assert True","action":null,"is_final":true}',
    )

    r = client.post(
        "/rlm",
        json={
            "query": "Generate a test for the analyze_code skill",
            "context": "",
            "depth": 0,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is True
    assert "codegen_output" in data["summary"]
    assert "Wrote" in data["summary"] or "bytes" in data["summary"]
    # File should exist under tmp_path/codegen_output/
    codegen_dir = tmp_path / "codegen_output"
    assert codegen_dir.exists()
    assert list(codegen_dir.glob("*.py"))


def test_rlm_vertical_code_review(monkeypatch, tmp_path):
    """Vertical code_review: is_final triggers analyze_code → run_tests → write_file_safe to reviewed/; summary contains 'reviewed' and write observation."""
    monkeypatch.setenv("PAGI_VERTICAL_USE_CASE", "code_review")
    monkeypatch.setenv("PAGI_ACTIONS_VIA_GRPC", "false")
    monkeypatch.setenv("PAGI_ALLOW_LOCAL_DISPATCH", "true")
    monkeypatch.setenv("PAGI_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("PAGI_CODE_REVIEW_OUTPUT_DIR", "reviewed")
    monkeypatch.delenv("PAGI_MOCK_MODE", raising=False)
    monkeypatch.setenv("PAGI_MOCK_MODE", "false")

    monkeypatch.setenv(
        "PAGI_RLM_STUB_JSON",
        '{"thought":"Proposed fix: add type hints and docstring.","action":null,"is_final":true}',
    )

    result = MagicMock()
    result.returncode = 0
    result.stdout = "1 passed"
    result.stderr = ""

    with patch("subprocess.run", return_value=result):
        r = client.post(
            "/rlm",
            json={
                "query": "Review this code for issues and propose fixes",
                "context": "code: def add(a, b): return a + b",
                "depth": 0,
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is True
    assert "reviewed" in data["summary"].lower() or "Code review" in data["summary"]
    assert "Wrote" in data["summary"] or "write" in data["summary"].lower() or "bytes" in data["summary"]
    review_dir = tmp_path / "reviewed"
    assert review_dir.exists()
    assert list(review_dir.glob("reviewed_*.py"))


def test_self_heal_grpc(monkeypatch):
    """When PAGI_ALLOW_SELF_HEAL_GRPC=true and ValidationError occurs, ProposePatch is called via gRPC."""
    monkeypatch.setenv("PAGI_ALLOW_SELF_HEAL_GRPC", "true")
    monkeypatch.setenv("PAGI_RLM_STUB_JSON", "not-json")
    monkeypatch.delenv("PAGI_MOCK_MODE", raising=False)

    mock_stub = MagicMock()
    mock_stub.ProposePatch.return_value = MagicMock(
        patch_id="p1", proposed_code="", requires_hitl=True
    )

    with patch("src.recursive_loop._get_grpc_stub", return_value=mock_stub):
        r = client.post(
            "/rlm",
            json={"query": "anything", "context": "", "depth": 0},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["converged"] is False
    assert "schema" in data["summary"].lower() or "enforcement" in data["summary"].lower()
    mock_stub.ProposePatch.assert_called_once()
    call_args = mock_stub.ProposePatch.call_args[0][0]
    assert call_args.error_trace
    assert "schema" in call_args.error_trace.lower() or "validation" in call_args.error_trace.lower()
    assert call_args.component == "python_skill"


def test_self_heal_grpc_propose(monkeypatch):
    """ProposePatch is called with error_trace and component when self-heal gRPC is enabled and error occurs."""
    monkeypatch.setenv("PAGI_ALLOW_SELF_HEAL_GRPC", "true")
    monkeypatch.setenv("PAGI_RLM_STUB_JSON", "not-json")

    mock_stub = MagicMock()
    mock_stub.ProposePatch.return_value = MagicMock(
        patch_id="propose-1", proposed_code="# fix", requires_hitl=True
    )

    with patch("src.recursive_loop._get_grpc_stub", return_value=mock_stub):
        client.post("/rlm", json={"query": "x", "context": "", "depth": 0})

    mock_stub.ProposePatch.assert_called_once()
    req = mock_stub.ProposePatch.call_args[0][0]
    assert req.error_trace
    assert req.component == "python_skill"


def test_self_heal_grpc_apply(monkeypatch):
    """When propose_resp.requires_hitl=false, ApplyPatch is called with approved=true."""
    monkeypatch.setenv("PAGI_ALLOW_SELF_HEAL_GRPC", "true")
    monkeypatch.setenv("PAGI_RLM_STUB_JSON", "not-json")

    mock_stub = MagicMock()
    mock_stub.ProposePatch.return_value = MagicMock(
        patch_id="auto-patch-1", proposed_code="# fix", requires_hitl=False
    )
    mock_stub.ApplyPatch.return_value = MagicMock(success=True, commit_hash="abc123")

    with patch("src.recursive_loop._get_grpc_stub", return_value=mock_stub):
        client.post("/rlm", json={"query": "x", "context": "", "depth": 0})

    mock_stub.ProposePatch.assert_called_once()
    mock_stub.ApplyPatch.assert_called_once()
    apply_req = mock_stub.ApplyPatch.call_args[0][0]
    assert apply_req.patch_id == "auto-patch-1"
    assert apply_req.approved is True
    assert apply_req.component == "python_skill"


def test_auto_evolve_from_patch():
    """evolve_skill_from_patch skill writes new skill file and returns EVOLVED_PATH for Watchdog commit."""
    from pathlib import Path

    from src.skills.evolve_skill_from_patch import EvolveSkillFromPatchParams, run

    params = EvolveSkillFromPatchParams(patch_content="# fix for null check")
    out = run(params)
    assert out.startswith("EVOLVED_PATH:")
    path_str = out.split(":", 1)[1].strip()
    bridge_root = Path(__file__).resolve().parent.parent
    full_path = (bridge_root / path_str.replace("\\", "/")).resolve()
    assert full_path.exists()
    full_path.unlink()
