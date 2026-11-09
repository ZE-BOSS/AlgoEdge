# Copy trading — changes waiting for deploy (9 Nov 2026)

**Status:** built and tested on branch `copytrade-next`. **Not deployed.** The VPS still runs
`dev` @ d8e52e7. Deploy only after today's session ends, during gold's daily close
(22:00–23:00 UTC = 23:00–00:00 Lagos).

## Deploy steps (after the session)

1. Check that no copied trade is half-way through a follow-up (Signal log is quiet).
2. Merge: `git checkout dev && git merge --ff-only copytrade-next && git push origin dev`
   (or merge without ff if dev moved).
3. In the Hostinger web console:
   `curl -fsSL https://raw.githubusercontent.com/ZE-BOSS/AlgoEdge/dev/deploy/linux/update.sh | bash`
4. Check that `/api/health` is ok on both instances and that Copy Trading shows *Connected* and watching 5 chats.
5. Change these settings on the Copy Trading page (demo first):
   - **Breakeven offset → 0** (or 0.01). Better to do this right away; it needs no deploy.
   - **Trail the stop up the TP ladder → on** (new).
   - **In groups, follow only the admins → on** (new, default on). Needed for **GTMoFx**, which is a group.
   - **Jamson** channel → *Symbol when the signal names none* = `XAUUSD` (some posts say "Take Entry SELL 4430" with no symbol).
   - *When the first zone order fills* → leave on **Keep** (see results: it only helped GTS VIP).

## What changed

### Fixes (today's missed breakeven)
- **Root cause (09:51 UTC, signal #8664, Gold platinum trader = a GTMoFx repost).** TP1 filled and leg 3 was
  cancelled correctly. Moving leg 2 (entry 4093.64) to breakeven used the 2% offset (capped at 0.1%) →
  stop 4097.73, **above** the bid. MT5 refused it, nothing retried, and the log still said "rest at breakeven".
- Breakeven and trailing stops are now **recomputed every 10-second round and retried** until MT5 accepts.
  An offset that would sit past the price **falls back to the plain entry**. Refusals are logged.
- A zone leg that **filled and hit its TP between two checks** was recorded as CANCELLED, so
  "cancel after TPn" and "breakeven after TPn" never saw it. Now it's recorded as CLOSED (checked against deal history).
- A "set breakeven" message while price is **behind the entry** is queued and applied as soon as it fits,
  instead of failing.

### New features
- **TP ladder trail** (`trail_ladder`): TP1 → breakeven, TP2 → TP1, TP3 → TP2 … for every open leg, as
  price reaches each posted target, even targets beyond the legs placed. Never loosens. Works in dry run too.
- **First fill → rest of the zone at market** (`on_first_fill = market`): when the first zone limit fills,
  the deeper unfilled limits are cancelled and their volume is taken at market at once.
- **Groups: admin-only** (`only_admins`, default on; per-channel `allowed_senders`). Previously any member's
  message in a watched group was acted on.
- **Add-on entries** ("ADD MORE BUY 4198 / UPDATE SL 4190", Jamson): joins the running trade, keeps its
  targets, and moves the whole trade's stop.
- **Default symbol per channel** (`default_symbol`), for posts that don't name one.
- The history endpoint returns up to 5000 messages and marks `sender_id` / `from_admin`.

### Parser (how the new channels write)
| Channel | Signals | Follow-ups it uses |
|---|---|---|
| **GTMoFx** (group, @goldtradermo) | `Gold buy now 4095.3 - 4092 / SL: 4087 / TP: 4097 … TP: open` (same text Gold platinum trader reposts) | "SET BREAKEVEN FOR ZERO RISK NOW", "move SL above entries", "Move/Adjust/Update SL to 4392", "closed half", "closing top entries… hold bottom ones", "IM CLOSING ALL NOW", "ALL ENTRIES CLOSED" |
| **TRADE WITH JAMSON** | `Take Entry XAUUSD BUY 4126/ 4123.00 / Stop Loss 4113 / TP 4131 … TP 4150` (6 TPs, no separator) | "ADD MORE BUY 4198 / NEW SL 4190", "Change SL 4420", "Moved SL 4440", "SL UPDATE 3890", "HOLD ALL TARGETS" (= nothing), "TP 2 HIT BOOM BOOM" (= info) |
| **FIB Copy Trade (Averis)** | multi-asset: `USDJPY Buy stop at 158.000 / SL: … / TP: …`; 2024 style `SL. 2453.773 / TP. 2476.6` | "Move SL to 2397", "Cancel order / Delete this order", "close all Trades Now", "Breakeven now and take some profit" |

Parser fixes: a bare "BE" only counts as breakeven where it can only mean that ("be disciplined", "what needs
to be done", "let be patient" were read as breakeven); questions never act ("Hold or close?"); candle and market
closes aren't trade closes ("3 minutes from M15 to close"); "SL." / "TP." labels; repeated "TP 4131 TP 4135"
targets; `XAUUSD_BUY`; "Moved SL 4440" / "SL UPDATE 3890"; "ALL ENTRIES CLOSED" / "I closed all now" = close all;
"closed all my worst positions" stays a partial close.

Tests: 36 copier tests pass (`tests/test_copytrade.py`, `tests/test_copytrade_trail.py`).

## Backtest results (1-minute bars, 29 Jul – 9 Nov 2026, 1% risk per signal, R = multiples of that risk)

| Channel | Signals filled | Copier as now | + ladder trail | best variant |
|---|---|---|---|---|
| **Jamson** | 102 / 108 | +22.2R, PF 1.85, DD 7.9R | +23.7R, PF 2.37, DD 4.6R | signals only + BE at TP1: +27.1R, PF 3.16, DD 2.5R |
| **GTS public** | 28 / 52 | +8.8R, PF 6.2, DD 0.9R | +7.2R, no losses | follow + BE at TP1: +9.6R |
| **GTS VIP** | 29 / 48 | +1.5R, PF 1.6 | +4.5R, PF 5.9, DD 0.9R | ladder + first fill at market: +5.4R, PF 5.0 |
| **GTMoFx** (= Gold platinum) | 57 / 74 | −1.5R, PF 0.92, DD 5.4R | −0.9R | none positive |
| **FIB (all symbols)** | ~80 | ≈ 0R in total | — | gold +1.3R, USDJPY +3.5R, GBPAUD +3.5R; BTCUSD −4.9R, USDCHF −2.9R, GBPUSD −1.9R |

Jamson made +22R of its total in August (Oct −7.5R). GTMoFx/Platinum was positive only in August.

## Flip study ($100 → $1,000, `scripts/copytrade_flip.py`)
1-minute data, 29 Jul – 9 Nov (3.4 months), one flip trade at a time, 0.01-lot steps:
- **Jamson, copier + ladder, 25% of the flip pot per signal:** 1 flip in ~20 days / 20 trades, no busts.
  At 50%: 2 flips (~10 days each) and 2 busts.
- **GTS public, 50%:** 1 flip in 66 days. **GTS VIP, 50% + first fill at market:** 1 flip in 68 days.
- **GTMoFx:** no flip; busts at 25–50%.
- **Pyramiding** (stacking at each target from floating profit, close-all at TP3–TP6) and **high-RR runners
  (1:5 / 1:10 / 1:20)**: **no flip on any channel**. The targets are about $2 apart, so trailing one target back
  gets stopped by normal noise before the stack grows; no runner got past 5R.
- **Stacking extra market orders on the first fill** (`market_add`): the fastest flips (8–50 days) and the most
  busts (Jamson at 50%: 1 flip, 7 busts).
- 17-month check (5-minute data, VIP Jun 2025 – Nov 2026, Jamson May – Nov 2026). On the VIP, copier flips took
  ~5 months each at 50% (2 flips, 1 bust) and ~13 months at 25%. With first-fill stacking at 50% it took ~39 days
  (5 flips, 10 busts). Pyramids did flip a few times there, but busted 2–5× as often as they flipped (best case:
  4 flips / 22 busts). Jamson flipped once in ~83 days at 25%, with no busts.

## Still open
- Telegram history from groups (GTMoFx) includes member messages. Re-export after deploy (it now marks
  `from_admin`) and re-run GTMoFx backtests on admin messages only.
- FIB non-gold symbols need the broker's symbol names checked (USDJPY, GBPAUD … on Deriv).
