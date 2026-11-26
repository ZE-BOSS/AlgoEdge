"""Live check of the group bot in one group: post, threaded reply, status-line edit, delete.
Everything posted is labelled as a test and deleted at the end. Run in the live app folder:
    ../venv/bin/python scripts/groupbot_live_test.py -1003622850842 [--tier GENERAL] [--keep]"""
import asyncio, os, sqlite3, sys
from pathlib import Path
from dotenv import dotenv_values

sys.path.insert(0, str(Path.cwd()))
os.environ.setdefault("GROUP_BOT_TOKEN", dotenv_values(".env").get("GROUP_BOT_TOKEN", ""))


async def main(chat: int, tier: str | None, keep: bool):
    from backend.groups.botapi import BotAPI
    from backend.groups import templates as T
    api = BotAPI()
    me = await api.get_me()
    print("bot:", me.get("username"))
    m = await api.chat_member(chat, me["id"])
    print("rights:", m.get("status"), {k: v for k, v in m.items() if k.startswith("can_") and v})
    ids = []
    root = await api.send(chat, "🔧 <b>Bot test</b> - please ignore, this will be deleted in a moment.\n\n"
                          + T.entry("XAUUSD", "BUY", 2650.0, 2640.0, [2660.0, 2670.0], chase_limit=2651.3), silent=True)
    ids.append(root["message_id"]); print("posted signal-format message", root["message_id"])
    r = await api.send(chat, "🔧 test reply: " + T.tp_hit(1, 2660.0, 1.0, move_be=True), reply_to=root["message_id"], silent=True)
    ids.append(r["message_id"]); print("threaded reply", r["message_id"])
    await api.edit(chat, root["message_id"], "🔧 <b>Bot test</b> - please ignore (edited: status line works).")
    print("edited the root message")
    await asyncio.sleep(1 if not keep else 0)
    if not keep:
        for i in ids:
            await api.delete(chat, i)
        print("deleted", len(ids), "test messages")
    if tier:
        c = sqlite3.connect("algoedge.db")
        c.execute("update signal_groups set tier = ?, posting = 1 where chat_id = ?", (tier.upper(), chat))
        c.commit(); print("tier", tier.upper(), "posting on")
    await api.close()
    print("LIVE_TEST_OK")


if __name__ == "__main__":
    a = sys.argv[1:]
    tier = a[a.index("--tier") + 1] if "--tier" in a else None
    asyncio.run(main(int(a[0]), tier, "--keep" in a))
