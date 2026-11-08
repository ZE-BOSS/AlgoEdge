"""
scripts/copytrade_export.py

Exports a connected channel's Telegram history to the TSV that
scripts/copytrade_backtest.py reads. Run it ON THE SERVER, next to the running
API: it asks that API for the history (so the one Telegram session the copier
already holds is reused - a second client on the same session could get it
revoked), signing a 5-minute token for the account itself.

    cd /opt/algoedge/demo/app && sudo -u algoedge ../venv/bin/python scripts/copytrade_export.py \
        --email you@example.com --channel "GTS VIP" --limit 3000 --out /tmp/gts_vip.tsv

Signals (messages that parse as OPEN) are written as S rows, everything else as
F rows with the message they reply to. Line breaks are kept as a literal \\n.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT.parent / "instance.env")
except ImportError:
    pass


async def lookup(email: str, channel: str):
    from sqlalchemy import select

    from backend.copytrade.models import CopyTradeChannel
    from backend.data.database import async_session
    from backend.data.models import User

    async with async_session() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            raise SystemExit(f"no user {email}")
        rows = (await s.execute(select(CopyTradeChannel).where(CopyTradeChannel.user_id == user.id))).scalars().all()
        want = channel.lower().lstrip("@")
        hit = [r for r in rows if want in (r.title or "").lower() or want == (r.username or "").lower()]
        if not hit:
            raise SystemExit("channel not found; have: " + ", ".join(r.title for r in rows))
        return str(user.id), hit[0]


def token(user_id: str) -> str:
    from jose import jwt

    from backend.api.deps import JWT_ALGORITHM, JWT_SECRET_KEY

    exp = datetime.now(timezone.utc) + timedelta(minutes=5)
    return jwt.encode({"sub": user_id, "type": "access", "exp": exp}, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--channel", required=True, help="title (or part of it) or @username")
    ap.add_argument("--limit", type=int, default=3000)
    ap.add_argument("--port", type=int, default=int(os.getenv("API_PORT", "8001")))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    user_id, ch = asyncio.run(lookup(a.email, a.channel))
    req = urllib.request.Request(f"http://127.0.0.1:{a.port}/api/copytrade/channels/{ch.id}/history?limit={a.limit}",
                                 headers={"Authorization": "Bearer " + token(user_id)})
    with urllib.request.urlopen(req, timeout=300) as r:
        msgs = json.loads(r.read())
    msgs.sort(key=lambda m: m["id"])

    n_s = 0
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(f"# {ch.title} - exported {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC, {len(msgs)} messages\n")
        f.write("# kind\tmsg_id\ttime_utc\tref\ttext\n")
        for m in msgs:
            t = datetime.fromisoformat(m["date"]).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            text = m["text"].replace("\t", " ").replace("\r", "").replace("\n", "\\n")
            if m["parsed"]["kind"] == "OPEN":
                n_s += 1
                f.write(f"S\t{m['id']}\t{t}\t0:NONE\t{text}\n")
            else:
                f.write(f"F\t{m['id']}\t{t}\t{m.get('reply_to') or 0}\t{text}\n")
    first = msgs[0]["date"][:10] if msgs else "-"
    last = msgs[-1]["date"][:10] if msgs else "-"
    print(f"{ch.title}: {len(msgs)} messages {first}..{last}, {n_s} signals -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
