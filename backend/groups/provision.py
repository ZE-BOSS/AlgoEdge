"""
backend/groups/provision.py

Provisioning a VVIP client's own instance. Driven by deploy/linux/add_client.sh:

    plan <client_id>                 (live venv)   -> "NAME API_PORT BRIDGE_PORT REDIS_DB"
    configure <client_id> <name>     (client venv, in the client's app folder,
                                      with its instance.env loaded)
    finish <client_id> <name>        (live venv)   -> marks the account ACTIVE
    remove <client_id>               (live venv)   -> marks it STOPPED, wipes the password

`configure` does everything that touches a secret, inside one process: it
reads the client's encrypted MT5 password from live's database, decrypts it
with live's ENCRYPTION_KEY, logs the client's terminal in through the
client's own bridge, and forgets it. Nothing is printed or written to disk.

The client instance gets: its own .env (live's settings minus live's
accounts and alert channels, plus the follower sync to live), its own
database with one admin user and live's strategy slots - symbols renamed to
the client's broker where they differ, the client's risk on every slot.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import uuid
from pathlib import Path

BASE = Path(os.getenv("ALGOEDGE_BASE", "/opt/algoedge"))
LIVE = Path(os.getenv("VVIP_LEADER_DIR", str(BASE / "live")))
FIRST_PORT, FIRST_BRIDGE = 8010, 18820
REDIS_DBS = range(4, 16)

# never copied from live into a client's .env
DROP_KEYS = re.compile(r"^(MT5_ACCOUNT|MT5_PASSWORD|MT5_SERVER|PORT|REDIS_URL|ALGOEDGE_SERVER_ROLE|ALGOEDGE_SERVER_LABEL|"
                       r"GROUP_BOT_TOKEN|GROUP_ADMIN_IDS|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID|PAYSTACK_SECRET_KEY|"
                       r"SIGNAL_SYNC\w*|VVIP_\w*|ORDERFLOW\w*|DATABENTO_API_KEY|DAILY_REPORT\w*|WATCH_ALERTS|DATABASE_URL)=")


def read_env(path: Path) -> dict[str, str]:
    out = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.replace("export ", "").strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def live_db() -> Path:
    url = read_env(LIVE / "app" / ".env").get("DATABASE_URL", "sqlite+aiosqlite:///algoedge.db")
    p = url.split(":///", 1)[-1]
    return Path(p) if p.startswith("/") else LIVE / "app" / p


def client_row(cid: str) -> dict:
    con = sqlite3.connect(live_db())
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM vvip_accounts WHERE id = ?", (int(cid),)).fetchone()
    con.close()
    if r is None:
        sys.exit(f"no VVIP account {cid}")
    return dict(r)


def used_resources() -> tuple[set[int], set[int], set[int]]:
    ports, bridges, dbs = set(), set(), set()
    for d in BASE.iterdir() if BASE.is_dir() else []:
        ie = read_env(d / "instance.env")
        if not ie:
            continue
        if ie.get("API_PORT", "").isdigit():
            ports.add(int(ie["API_PORT"]))
        if ie.get("BRIDGE_PORT", "").isdigit():
            bridges.add(int(ie["BRIDGE_PORT"]))
        m = re.search(r"/(\d+)$", read_env(d / "app" / ".env").get("REDIS_URL", ""))
        if m:
            dbs.add(int(m.group(1)))
    return ports, bridges, dbs


def plan(cid: str) -> str:
    row = client_row(cid)
    if not row.get("mt5_password_encrypted"):
        sys.exit("the client has no stored MT5 password (disconnected?) - they must connect again on the portal")
    name = row.get("instance") or f"client{cid[-6:]}"
    if (BASE / name / "instance.env").exists():
        ie = read_env(BASE / name / "instance.env")
        m = re.search(r"/(\d+)$", read_env(BASE / name / "app" / ".env").get("REDIS_URL", ""))
        return f"{name} {ie.get('API_PORT')} {ie.get('BRIDGE_PORT')} {m.group(1) if m else 4}"
    ports, bridges, dbs = used_resources()
    port = next(p for p in range(FIRST_PORT, FIRST_PORT + 200) if p not in ports)
    bridge = next(b for b in range(FIRST_BRIDGE, FIRST_BRIDGE + 200) if b not in bridges)
    free = [d for d in REDIS_DBS if d not in dbs]
    if not free:
        sys.exit("no free Redis database number left (4-15) - the server is at its client limit")
    return f"{name} {port} {bridge} {free[0]}"


def symbol_candidates(sym: str) -> list[str]:
    s = sym.strip()
    out = [s]
    base = re.sub(r"(m|\.|\.a|\.r|_i|-ecn|\.pro)$", "", s, flags=re.I)
    for v in (base, base + "m", base + ".", base + ".a", base + "c", base.upper()):
        if v not in out:
            out.append(v)
    return out


def map_symbols(slots: list[dict], exists) -> dict[str, str]:
    """Leader symbol -> the client's name for it, for every slot symbol the client's broker spells differently."""
    out = {}
    for sl in slots:
        sym = sl.get("symbol") or ""
        if not sym or sym in out:
            continue
        for c in symbol_candidates(sym):
            if exists(c):
                if c != sym:
                    out[sym] = c
                break
        else:
            print(f"!! {sym}: not offered by the client's broker - that slot is switched off")
            out[sym] = ""
    return out


def leader_config() -> dict:
    con = sqlite3.connect(live_db())
    rows = con.execute(
        "SELECT c.config_json FROM user_configs c JOIN users u ON u.id = c.user_id "
        "WHERE u.is_admin = 1 ORDER BY c.updated_at DESC").fetchall()
    con.close()
    if not rows:
        sys.exit("live has no admin strategy config to copy")
    return json.loads(rows[0][0])


def client_env(cid: str, name: str, row: dict, port: int, redis_db: int, symbol_map: dict[str, str]) -> str:
    lines = [f"# AlgoEdge VVIP client {cid} ({name}) - written by add_client.sh. Never commit."]
    for line in (LIVE / "app" / ".env").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and not DROP_KEYS.match(line.strip().replace("export ", "")):
            lines.append(line)
    sm = ",".join(f"{a}={b}" for a, b in symbol_map.items() if b)
    lines += [
        "HOST=127.0.0.1", f"PORT={port}", "DATABASE_URL=sqlite+aiosqlite:///algoedge.db",
        f"REDIS_URL=redis://localhost:6379/{redis_db}",
        "ALGOEDGE_SERVER_ROLE=live", f"ALGOEDGE_SERVER_LABEL=VVIP client {row.get('mt5_login')}",
        f"MT5_ACCOUNT={row.get('mt5_login')}", f"MT5_SERVER={row.get('mt5_server')}", "MT5_PASSWORD=",
        f"SIGNAL_SYNC_SELF={name}", "SIGNAL_SYNC_PEERS=live", "SIGNAL_SYNC_FOLLOW=1",
        f"SIGNAL_SYNC_SYMBOL_MAP={sm}",
        f"VVIP_CLIENT={cid}", f"VVIP_DAILY_LOSS_PCT={row.get('daily_loss_pct') or 5}",
        f"VVIP_MAX_DD_PCT={row.get('max_dd_pct') or 20}",
        "DAILY_REPORT=0", "WATCH_ALERTS=0",
    ]
    return "\n".join(lines) + "\n"


def client_config(leader: dict, symbol_map: dict[str, str], risk: float, daily_loss: float) -> dict:
    from backend.groups.client_guard import set_risk
    cfg = json.loads(json.dumps(leader))
    for sl in cfg.get("instrument_slots") or []:
        sym = sl.get("symbol") or ""
        if sym in symbol_map:
            if symbol_map[sym]:
                sl["symbol"] = symbol_map[sym]
            else:
                sl["enabled"] = False
    cfg.pop("telegram_bot_token", None)
    cfg.pop("telegram_chat_id", None)
    cfg["notify_signal"] = False
    return set_risk(cfg, min(2.0, max(0.1, float(risk or 1))), daily_loss)


def configure(cid: str, name: str) -> None:
    import asyncio
    from cryptography.fernet import Fernet

    app = Path.cwd()
    row = client_row(cid)
    live_env = read_env(LIVE / "app" / ".env")
    key = live_env.get("ENCRYPTION_KEY", "")
    if not key or not row.get("mt5_password_encrypted"):
        sys.exit("cannot decrypt the client's password (no ENCRYPTION_KEY on live, or no stored password)")
    ie = read_env(app.parent / "instance.env")
    port = int(ie["API_PORT"])
    m = re.search(r"/(\d+)$", read_env(app / ".env").get("REDIS_URL", ""))
    redis_db = int(sys.argv[4]) if len(sys.argv) > 4 else (int(m.group(1)) if m else 4)

    # 0. this instance's settings first (backend reads .env when imported)
    (app / ".env").write_text(client_env(cid, name, row, port, redis_db, {}))
    os.chmod(app / ".env", 0o600)
    os.environ.update(read_env(app / ".env"))

    # 1. log the client's terminal in (through this instance's own bridge)
    import backend  # noqa: F401  - installs this instance's MT5 bridge
    import MetaTrader5 as mt5

    from backend.mt5.executor import reinitialize
    if not reinitialize(mt5):                      # pinned to this instance's terminal
        sys.exit(f"cannot reach the client's MT5 terminal: {mt5.last_error()}")
    pw = Fernet(key.encode()).decrypt(row["mt5_password_encrypted"].encode()).decode()
    ok = mt5.login(int(row["mt5_login"]), password=pw, server=row["mt5_server"])
    del pw
    if not ok:
        sys.exit(f"MT5 login failed for {row['mt5_login']} on {row['mt5_server']}: {mt5.last_error()} "
                 "- check the login, server and password the client entered")
    acc = mt5.account_info()
    print(f"== logged in: {acc.login} {acc.server} balance {acc.balance:,.2f} {acc.currency} leverage 1:{acc.leverage}")

    # 2. the leader's slots, renamed for this broker
    leader = leader_config()
    slots = [s for s in (leader.get("instrument_slots") or []) if s.get("enabled", True)]
    smap = map_symbols(slots, lambda s: mt5.symbol_select(s, True) and mt5.symbol_info(s) is not None)
    for a, b in smap.items():
        if b:
            print(f"   {a} -> {b}")
    cfg = client_config(leader, smap, row.get("risk_pct"), row.get("daily_loss_pct"))

    # 3. this instance's settings (now with the symbol map) and database
    (app / ".env").write_text(client_env(cid, name, row, port, redis_db, smap))
    os.chmod(app / ".env", 0o600)
    from backend.core.passwords import hash_password
    from backend.data.database import init_db
    asyncio.run(init_db())
    db = sqlite3.connect(app / "algoedge.db")
    uid = db.execute("SELECT id FROM users WHERE is_admin = 1").fetchone()
    uid = uid[0] if uid else str(uuid.uuid4())
    db.execute("INSERT OR REPLACE INTO users (id, email, password_hash, name, mt5_account, mt5_server, is_active, is_admin, "
               "subscription_status, active_strategy, risk_per_trade, max_daily_loss) VALUES (?,?,?,?,?,?,1,1,'active','',?,?)",
               (uid, f"client{cid}@vvip.local", hash_password(uuid.uuid4().hex), f"VVIP {row['mt5_login']}",
                int(row["mt5_login"]), row["mt5_server"], float(row.get("risk_pct") or 1), float(row.get("daily_loss_pct") or 5)))
    db.execute("INSERT OR REPLACE INTO user_configs (user_id, config_json, preset_name) VALUES (?,?,?)",
               (uid, json.dumps(cfg), "vvip"))
    db.commit()
    db.close()
    on = [s["symbol"] for s in cfg.get("instrument_slots") or [] if s.get("enabled", True)]
    print(f"== {len(on)} slots copied from live at {cfg['risk']['risk_per_trade_pct']}% risk: {', '.join(sorted(set(on)))}")


def finish(cid: str, name: str, state: str = "ACTIVE") -> None:
    con = sqlite3.connect(live_db())
    con.execute("UPDATE vvip_accounts SET instance = ?, state = ?, paused_reason = '' WHERE id = ?", (name, state, int(cid)))
    con.commit()
    con.close()
    print(f"== client {cid}: instance {name}, {state}")


def remove(cid: str) -> str:
    row = client_row(cid)
    con = sqlite3.connect(live_db())
    con.execute("UPDATE vvip_accounts SET state = 'STOPPED', mt5_password_encrypted = '', paused_reason = ? WHERE id = ?",
                ("removed by the admin", int(cid)))
    con.commit()
    con.close()
    return row.get("instance") or ""


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "plan":
        print(plan(sys.argv[2]))
    elif cmd == "configure":
        configure(sys.argv[2], sys.argv[3])
    elif cmd == "finish":
        finish(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "ACTIVE")
    elif cmd == "remove":
        print(remove(sys.argv[2]))
    else:
        sys.exit("usage: provision.py plan|configure|finish|remove <client_id> [name]")
