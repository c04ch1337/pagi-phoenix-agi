"""Minimal tests for Phase 3 RLM REPL (no outbound calls)."""

import os

import pytest
from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


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
