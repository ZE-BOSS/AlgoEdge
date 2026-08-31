"""FundedNext connection + symbol discovery.

Credentials come from the environment (FN_LOGIN / FN_PASS / FN_SERVER) so they
are never written into this file or the repo.
"""
import os
import MetaTrader5 as mt5

SEP = chr(92)  # backslash, avoids escaping trouble in shell heredocs


def connect():
    login = os.environ.get("FN_LOGIN")
    if not login:
        raise SystemExit("FN_LOGIN/FN_PASS/FN_SERVER not set in the environment")
    ok = mt5.initialize(login=int(login),
                        password=os.environ["FN_PASS"],
                        server=os.environ["FN_SERVER"])
    if not ok and mt5.initialize():
        ok = mt5.login(int(login), password=os.environ["FN_PASS"],
                       server=os.environ["FN_SERVER"])
    return ok


if __name__ == "__main__":
    if not connect():
        raise SystemExit(f"connect failed: {mt5.last_error()}")
    ai = mt5.account_info()
    print(f"account {ai.login} | {ai.server} | {ai.company} | {ai.currency} "
          f"| balance {ai.balance:,.2f} | leverage 1:{ai.leverage}")
    syms = mt5.symbols_get()
    print(f"\n{len(syms)} symbols\n")
    groups = {}
    for s in syms:
        g = s.path.split(SEP)[0] if s.path else "?"
        groups.setdefault(g, []).append(s.name)
    for g in sorted(groups):
        print(f"[{g}]")
        print("   " + ", ".join(sorted(groups[g])))
    mt5.shutdown()
