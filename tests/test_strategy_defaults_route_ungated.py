"""`/api/strategy-factory/strategy-defaults` must answer with the factory OFF.

It sat behind the factory's opt-in gate, so on every server without the env
flag it was a 404, and the Trading Book and the Backtester's slot editor
silently lost the measured exits and the "no measured target" warning.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app(monkeypatch):
    from backend.api.routes import strategy_factory as sf

    monkeypatch.setattr(sf, "strategy_factory_enabled", lambda: False)
    app = FastAPI()
    app.include_router(sf.router)
    app.include_router(sf.read_router)
    app.dependency_overrides[sf.get_current_user] = lambda: type("U", (), {"id": "u1"})()
    return app


def test_the_defaults_table_answers_with_the_factory_off(monkeypatch):
    r = TestClient(_app(monkeypatch)).get("/api/strategy-factory/strategy-defaults")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["strategy_defaults"], "empty defaults table"
    assert "ORB_v1" in body["strategy_defaults"] or len(body["strategy_defaults"]) > 3


def test_the_factory_itself_stays_gated(monkeypatch):
    r = TestClient(_app(monkeypatch)).get("/api/strategy-factory/strategies")
    assert r.status_code == 404
