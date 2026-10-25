"""Per-market $10k tables from results/fib_v2.json."""
import json, sys
R = json.loads(open("results/fib_v2.json").read())
ORDER = ["XAUUSD", "US Tech 100", "US SP 500", "Wall Street 30", "XAGUSD", "GBPJPY", "EURUSD", "BTCUSD",
         "Step Index 200", "Step Index 500", "Step Index", "Step Index 300", "Step Index 400", "Multi Step 2 Index",
         "Volatility 10 Index", "Volatility 25 Index", "Volatility 50 Index", "Volatility 75 Index", "Volatility 100 Index", "Volatility 75 (1s) Index"]
for name in [k for k in R if any(k.startswith(p) for p in sys.argv[1:])]:
    print(f"\n### {name}   ($10,000 per market, 1% risk compounded)")
    print(f"{'market':26s} | {'9m n':>4s} {'win%':>5s} {'exp R':>7s} {'return':>8s} {'DD':>5s} | {'3y n':>4s} {'win%':>5s} {'exp R':>7s} {'RR':>5s} {'return':>8s} {'DD':>5s}")
    for s in ORDER:
        a, b = R[name]["9m"]["markets"][s], R[name]["3y"]["markets"][s]
        f = lambda x: f"{x['n']:4d} {x['win']:5.1f} {x['exp']:+7.3f} {x['ret']:+7.1f}% {x['dd']:4.1f}%" if x.get("n") else f"{0:4d} {'':5s} {'':7s} {'':8s} {'':5s}"
        g = lambda x: f"{x['n']:4d} {x['win']:5.1f} {x['exp']:+7.3f} {x['rr']:5.2f} {x['ret']:+7.1f}% {x['dd']:4.1f}%" if x.get("n") else "   0"
        print(f"{s:26s} | {f(a)} | {g(b)}")
    for book in ("real", "synth"):
        a, b = R[name]["9m"][book], R[name]["3y"][book]
        print(f"{'BOOK ' + book + ' (one $10k acct)':26s} | {a['n']:4d} {a['win']:5.1f} {a['exp']:+7.3f} {a['ret']:+7.1f}% {a['dd']:4.1f}% | {b['n']:4d} {b['win']:5.1f} {b['exp']:+7.3f} {b['rr']:5.2f} {b['ret']:+7.1f}% {b['dd']:4.1f}%")
