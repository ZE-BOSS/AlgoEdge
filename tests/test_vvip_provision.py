"""VVIP client provisioning (backend/groups/provision.py) against a fake /opt/algoedge."""
from __future__ import annotations

import json
import sqlite3

import pytest


@pytest.fixture
def fake(tmp_path, monkeypatch):
    base = tmp_path / "algoedge"
    for name, port, bridge, db in (("demo", 8001, 18811, 1), ("live", 8002, 18812, 2), ("flip", 8003, 18813, 3)):
        (base / name / "app").mkdir(parents=True)
        (base / name / "instance.env").write_text(f"API_PORT={port}\nBRIDGE_PORT={bridge}\n")
        (base / name / "app" / ".env").write_text(
            f"PORT={port}\nREDIS_URL=redis://localhost:6379/{db}\nDATABASE_URL=sqlite+aiosqlite:///algoedge.db\n"
            "JWT_SECRET_KEY=jwt\nENCRYPTION_KEY=enc\nMT5_ACCOUNT=1\nMT5_PASSWORD=livepw\nMT5_SERVER=Exness\n"
            "TELEGRAM_BOT_TOKEN=alerts\nGROUP_BOT_TOKEN=grp\nANTHROPIC_API_KEY=ak\nSIGNAL_SYNC_PEERS=demo\n")
    con = sqlite3.connect(base / "live" / "app" / "algoedge.db")
    con.execute("CREATE TABLE vvip_accounts (id INTEGER PRIMARY KEY, mt5_login TEXT, mt5_server TEXT, "
                "mt5_password_encrypted TEXT, risk_pct REAL, daily_loss_pct REAL, max_dd_pct REAL, instance TEXT, "
                "state TEXT, paused_reason TEXT)")
    con.execute("INSERT INTO vvip_accounts VALUES (1795000000000123, '87654321', 'Exness-MT5Real8', 'tok', 1.5, 4, 20, '', 'NEW', '')")
    con.execute("CREATE TABLE users (id TEXT, is_admin INTEGER)")
    con.execute("CREATE TABLE user_configs (user_id TEXT, config_json TEXT, updated_at TEXT)")
    con.execute("INSERT INTO users VALUES ('u1', 1)")
    con.execute("INSERT INTO user_configs VALUES ('u1', ?, '2026-11-24')", (json.dumps({
        "risk": {"risk_per_trade_pct": 0.5}, "telegram_bot_token": "x",
        "instrument_slots": [{"symbol": "XAUUSDm", "strategy_id": "RQ_NYORB", "enabled": True},
                             {"symbol": "USTECm", "strategy_id": "QL_IBS", "enabled": True},
                             {"symbol": "Volatility 75 Index", "strategy_id": "X", "enabled": True}]}),))
    con.commit()
    con.close()
    import backend.groups.provision as P
    monkeypatch.setattr(P, "BASE", base)
    monkeypatch.setattr(P, "LIVE", base / "live")
    return P, base


def test_plan_picks_free_ports_and_redis_db(fake):
    P, base = fake
    assert P.plan("1795000000000123") == "client000123 8010 18820 4"
    (base / "client000999" / "app").mkdir(parents=True)
    (base / "client000999" / "instance.env").write_text("API_PORT=8010\nBRIDGE_PORT=18820\n")
    (base / "client000999" / "app" / ".env").write_text("REDIS_URL=redis://localhost:6379/4\n")
    assert P.plan("1795000000000123") == "client000123 8011 18821 5"


def test_plan_refuses_without_password(fake):
    P, base = fake
    con = sqlite3.connect(base / "live" / "app" / "algoedge.db")
    con.execute("UPDATE vvip_accounts SET mt5_password_encrypted = ''")
    con.commit()
    with pytest.raises(SystemExit):
        P.plan("1795000000000123")


def test_symbol_map_for_the_clients_broker(fake):
    P, _ = fake
    offered = {"XAUUSD", "USTEC"}
    slots = P.leader_config()["instrument_slots"]
    m = P.map_symbols(slots, lambda s: s in offered)
    assert m == {"XAUUSDm": "XAUUSD", "USTECm": "USTEC", "Volatility 75 Index": ""}
    assert P.map_symbols(slots, lambda s: True) == {}


def test_client_env_drops_live_accounts_and_alerts(fake):
    P, _ = fake
    row = P.client_row("1795000000000123")
    env = P.client_env("1795000000000123", "client000123", row, 8010, 4, {"XAUUSDm": "XAUUSD", "Volatility 75 Index": ""})
    for gone in ("livepw", "TELEGRAM_BOT_TOKEN=alerts", "GROUP_BOT_TOKEN", "SIGNAL_SYNC_PEERS=demo", "MT5_ACCOUNT=1\n"):
        assert gone not in env
    for kept in ("JWT_SECRET_KEY=jwt", "ENCRYPTION_KEY=enc", "ANTHROPIC_API_KEY=ak", "PORT=8010",
                 "REDIS_URL=redis://localhost:6379/4", "MT5_ACCOUNT=87654321", "SIGNAL_SYNC_PEERS=live",
                 "SIGNAL_SYNC_FOLLOW=1", "SIGNAL_SYNC_SYMBOL_MAP=XAUUSDm=XAUUSD\n", "VVIP_CLIENT=1795000000000123",
                 "VVIP_DAILY_LOSS_PCT=4", "DAILY_REPORT=0"):
        assert kept in env, kept


def test_client_config_renames_disables_and_sets_risk(fake):
    P, _ = fake
    cfg = P.client_config(P.leader_config(), {"XAUUSDm": "XAUUSD", "Volatility 75 Index": ""}, 1.5, 4)
    s = {x["strategy_id"]: x for x in cfg["instrument_slots"]}
    assert s["RQ_NYORB"]["symbol"] == "XAUUSD" and s["QL_IBS"]["symbol"] == "USTECm"
    assert s["X"]["enabled"] is False
    assert all(x["risk_per_trade_pct"] == 1.5 for x in cfg["instrument_slots"])
    assert cfg["risk"]["max_daily_drawdown_pct"] == 4 and "telegram_bot_token" not in cfg
    assert P.client_config(P.leader_config(), {}, 9, None)["risk"]["risk_per_trade_pct"] == 2.0


def test_finish_and_remove(fake):
    P, base = fake
    P.finish("1795000000000123", "client000123", "ACTIVE")
    con = sqlite3.connect(base / "live" / "app" / "algoedge.db")
    assert con.execute("SELECT instance, state FROM vvip_accounts").fetchone() == ("client000123", "ACTIVE")
    assert P.remove("1795000000000123") == "client000123"
    assert con.execute("SELECT state, mt5_password_encrypted FROM vvip_accounts").fetchone() == ("STOPPED", "")
