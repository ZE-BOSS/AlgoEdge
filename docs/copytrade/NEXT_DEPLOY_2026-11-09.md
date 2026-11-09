# Copy trading — changes waiting for deploy (9 Nov 2026)

**Status:** merged into `dev` (eb4bbeb) and pushed. **Not yet running on the VPS:** the automated deploy was
blocked by this session's safety check on production deploys. Run step 3 yourself (Chris said trading
was over for the day at 14:07 Lagos).

## Deploy steps (after the session)

1. Check that no copied trade is half-way through a follow-up (Signal log is quiet).
2. ~~Merge~~ done: `dev` already has everything.
3. In the Hostinger web console:
   `curl -fsSL https://raw.githubusercontent.com/ZE-BOSS/AlgoEdge/dev/deploy/linux/update.sh | bash`
4. Check that `/api/health` is ok on both instances and that Copy Trading shows *Connected* and watching 5 chats.
5. Change these settings on the Copy Trading page (demo first):
   - **Breakeven offset → 0** (or 0.01). Better to do this right away; it needs no deploy.
   - **Trail the stop up the TP ladder → on** (new).
   - **In groups, follow only the admins → on** (new, default on). Needed for **GTMoFx**, which is a group.
   - **Jamson** channel → *Symbol when the signal names none* = `XAUUSD` (some posts say "Take Entry SELL 4430" with no symbol).
   - *When the first zone order fills* → **Also take the rest at market (limits stay until TP1)**. Chris asked for
     this; it was the best variant on GTS VIP, GTS public and Jamson (see the account study below).
   - *Cancel unfilled orders once TP n fills* = 1 (already set).
   - Flip account only: optionally *Stack at TP1* = 1 and *Last leg runs without a target* = on.

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
- **First fill → rest of the zone at market** (`on_first_fill`):
  - `market`: cancel the deeper limits and take their volume at market.
  - `market_add` (added in the afternoon): take the remaining legs' volume at market AND leave the limits
    waiting. They are cancelled once TP1 fills, as before. Nothing is added if price is already at TP1, and a
    leg whose own TP is already behind the price is skipped. More size: if the limits fill later, those legs
    are doubled.
- **Runner leg** (`runner_leg`): the last leg has no target; the ladder trail (or his close) ends it.
- **Stack at TP1** (`stack_at_tp1` = multiple of the open volume): at TP1, add a market position with its stop
  at the first entry (it risks only the TP1 distance) and no target; the ladder trails it with the rest.
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

## FIB re-test, signal by signal (afternoon)
1-minute bars, 3 Aug – 9 Nov, every symbol it posted, copier as configured (1% per signal). "≈0R" meant the
net across symbols, not that nothing traded: about 103 trades, ~50 wins and ~50 losses.

| Symbol | Filled | Net | Symbol | Filled | Net |
|---|---|---|---|---|---|
| USDJPY | 12 | +3.5R | GBPUSD | 11 | −1.9R |
| GBPAUD | 9 | +3.5R | GBPJPY | 4 | −2.5R |
| EURAUD | 2 | +2.0R | USDCHF | 8 | −2.9R |
| USDCAD | 4 | +1.6R | EURJPY | 3 | −3.0R |
| XAUUSD | 19 | +1.4R | BTCUSD | 6 | −4.9R |
| EURCAD / EURUSD | 3 / 7 | +1.0R each | CHFJPY / EURNZD | 2 / 2 | −0.6R / −0.8R |
| GBPNZD / GBPCAD | 2 / 9 | +0.7R / +0.3R | | | |

Net ≈ −1.5R; without BTCUSD ≈ +3.3R. Its targets are tiny (TP1 often 0.2–0.4R), so a TP1-then-stop trade loses
~0.7R: many of the losses are "hit TP1, then stopped". The copier has not executed any FIB signal live; the
channel was added at 11:40 UTC today. With 5-minute bars back to Jul 2025: EURUSD +3.9R, GBPAUD +5.6R and
USDJPY +3.7R hold up; BTCUSD −5.6R, USDCHF −3.4R and EURJPY −3.0R stay negative.
→ If kept, follow only USDJPY, GBPAUD, EURUSD and gold (*Only these symbols* on the channel).

## Flip account traded like a normal account (`scripts/copytrade_account.py`)
$1,000, a fixed % of the balance risked per signal, never reset; overlapping trades allowed.
1-minute data, 29 Jul – 5 Nov, Jamson + GTS public + GTS VIP together:

| How | Trades | Sum R | 2% risk | 5% risk | 10% risk |
|---|---|---|---|---|---|
| copier (ladder on) | 154 | +33.9 | $1,873, DD 6% | $4,180, DD 16% | $12,641, DD 32% |
| **copier + first-fill market add** | 154 | +46.3 | $2,363, DD 13% | $7,079, DD 32% | $30,045, DD 60% |
| one position, ladder only | 151 | +34.2 | $1,571, DD 9% | $3,852, DD 30% | $11,471, DD 55% |
| one position, half at TP1 | 151 | +24.1 | $1,392, DD 11% | $2,785, DD 31% | $7,217, DD 56% |
| one position + stack at TP1 | 151 | +52.3 | $1,807, DD 12% | $5,762, DD 36% | $16,780, DD 61% |

17-month check (5-minute data, Jun 2025 – Nov 2026), GTS VIP + Jamson:
copier +44.5R (5%: $6,859, DD 20%); **copier + market add +69.2R (5%: $18,624, DD 29%)**; ladder + stack +55.7R
(5%: $5,345, DD 34%). The VIP alone: copier with the ladder +22.5R, where yesterday (no ladder) it was −4.6R.
The ladder trail is what turns the VIP positive. GTMoFx loses in every variant.

## Still open
- Telegram history from groups (GTMoFx) includes member messages. Re-export after deploy (it now marks
  `from_admin`) and re-run GTMoFx backtests on admin messages only.
- FIB non-gold symbols need the broker's symbol names checked (USDJPY, GBPAUD … on Deriv).
