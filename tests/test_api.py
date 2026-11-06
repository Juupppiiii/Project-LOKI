"""Tests for the FastAPI endpoints."""

from fastapi.testclient import TestClient

from bucky.api import app

client = TestClient(app)


def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "model" in data


def test_index_status():
    resp = client.get("/api/index/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "indexed" in data
