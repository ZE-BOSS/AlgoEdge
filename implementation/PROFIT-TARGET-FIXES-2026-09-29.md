# Profit targets — why they did nothing, and what changed

**2026-09-29** · `backend/risk/profit_target.py`, `circuit_breaker.py`,
`services/bot_service.py`, `core/config_schema.py`, `components/slotSpec.js`
· `tests/test_profit_target_live_bugs.py`

---

## The report

Two Crash 1000 Index positions open at **+$97.54** and **+$52.83** against a per-trade
target of **$50** (scope `TRADE`, basis `FLOATING`, action `CLOSE_AND_PAUSE`). Neither
closed. Nothing appeared in the log. The dashboard displayed the floating P&L the whole
time — so the number the target needed was on screen while the target ignored it.

That last detail is what made it look like the feature had never been wired up. It had;
it was reading from the wrong place.

---

## Root cause

`CircuitBreaker.active_groups` is populated in exactly one place: when **this process**
opens a trade. Nothing rehydrates it — not on bot start, not from open MT5 positions,
not from the database. `note_group_floating` then began:

```python
group = self.active_groups.get(group_id)
if group is None:
    return                      # <- every surviving position, dropped here
```

`check_profit_target` builds its numbers from `active_groups` too, so after a bot
restart it evaluated an **empty dict**, found nothing at or above target, and returned
`None`. No exception, no warning, no behavioural difference from "no target set".

The attribution was already established *before* this point — `bot_service` matches
`Trade.group_id`, symbol and strategy to the slot before it calls. Membership of
`active_groups` was a redundant second gate, and it was the fragile one.

**Why no test caught it:** every existing test registered its groups through the normal
open-a-trade path, so `active_groups` was always populated. The bug only exists for
positions that outlive the process that opened them — which is the normal state of a
bot that gets restarted, and never the state of a unit test.

---

## The five changes

### 1. Adopt positions the breaker did not open — *the fix*

`note_group_floating` now registers an unknown group instead of dropping it, logging
that it did so. `initial_risk` is `0.0` because it is genuinely unknown for a position
this process did not open; that under-counts `get_open_risk()` slightly, which is
strictly better than the position being invisible to every group-keyed check.

A position with **no** `group_id` at all still cannot be targeted — it cannot be
attributed to a slot — but that now logs a **warning** rather than passing silently,
because it means `Trade.group_id` was never written and the target is inert.

### 2. Close every trade that has reached the target, not just the first

`_check_trade` returned on the first qualifying group. With two trades past target only
one was banked per evaluation — live, that is one per 60-second scan cycle, so the
second ran on for a minute with its profit already made. It now returns all of them in
one hit.

### 3. A new `CLOSE` action — "bank it and carry on"

You asked for a third option that closes without pausing. Actions are now:

| Action | Closes | Pauses the slot |
|---|---|---|
| `CLOSE` **(new, now the default)** | yes | **no** |
| `PAUSE` | no | yes |
| `CLOSE_AND_PAUSE` | yes | yes |

`CLOSE_AND_PAUSE` on a *daily* target means one good morning ends your trading day,
which is rarely what anyone wants. `CLOSE` takes the money off the table and leaves the
slot free for the next signal.

**A `TRADE`-scope target never pauses, whatever this is set to.** A per-trade target
that stopped the slot would end the day on its first winner.

### 4. The day is now West African, not UTC

Every daily, weekly and monthly counter rolled over at **midnight UTC — 1am WAT** — so
"today's profit" reset an hour into your trading day, and a Monday session could be
judged against two different calendar days. All three boundaries now use an accounting
clock at **UTC+1**, configurable per account via `accounting_utc_offset_hours` (WAT has
no daylight saving, so a fixed offset is exact).

This shifts the *boundary* only. Timestamps are still stored in UTC, and backtest bar
times are treated as UTC before shifting, so a backtest day and a live day are the same
day.

### 5. A latent percentage bug

With a *percent* per-trade target, a group whose start balance was unknown made the
loop `return` — disabling the target for every other open group too. It now skips that
group and judges the rest.

---

## What a "trade" means, and pyramiding

You asked whether the per-trade target accounts for pyramided trades. It does, and here
is the exact rule:

- **A group is one signal.** `bot_service` mints a fresh `group_id` per signal.
- **Legs of one signal are summed.** A scaled-in position with two fills under one
  signal is one trade; the target sees their combined P&L.
- **Separate signals are separate trades, judged independently.** Pyramiding on the
  same symbol creates a second group, and each is measured against the target on its
  own.

Your two positions had different volumes (9.38 and 7.86) and different entries, so they
were two signals — two groups — each judged against $50 on its own. Both were past it;
both should have closed. They now do.

Nothing about pyramiding itself was changed, today or in the profit-target work. If the
scaling behaviour looks different from before, that is worth a separate look — tell me
what it used to do and I will trace it.

---

## The backtest showing $700 on a $50 target

This one is not a bug, and the distinction matters.

A `TRADE`-scope target caps **each trade**, not the run. Fourteen trades each banked at
$50 is a $700 return, and that is the target working exactly as specified. Evidence
from the end-to-end run on the real route:

```
TrendBreakout_v1 / XAUUSD  [+per-trade $150 floating target]
   435 signals -> 40 trades | net $1,409.98 | exits {TRAIL_SL: 20, PROFIT_TARGET: 20}
```

20 trades exited on `PROFIT_TARGET`, each at ~$150, totalling ~$1,400. Per trade, not
per run.

**If you want the run itself to stop at a number, that is `DAY` / `WEEK` / `MONTH`
scope**, which measures the slot's accumulated profit over the period rather than one
trade's. You can arm both at once — a $50 per-trade target and a $500 daily target
coexist, and whichever is reached first acts.

---

## Proof

`tests/test_profit_target_live_bugs.py` — 15 tests, including your exact scenario:

```python
positions = [
    {"group_id": "g-97", "symbol": "Crash 1000 Index", "profit": 97.54},
    {"group_id": "g-52", "symbol": "Crash 1000 Index", "profit": 52.83},
]
to_close = note_and_check(cb, positions, lambda p: p["profit"], 3585.44)
assert set(to_close) == {"g-97", "g-52"}
```

Against the old code this returns `[]`. It also pins: a trade at $49.99 is left alone;
a missing `group_id` warns; pyramided legs sum; separate signals are independent;
`CLOSE` does not pause and `CLOSE_AND_PAUSE` does; `TRADE` scope never pauses under any
action; 23:30 UTC is already tomorrow in WAT and 22:00 is not; the offset is
configurable back to UTC; and `FLOATING` counts an open trade where `BALANCE` does not.

Existing profit-target tests: **32 passed, unchanged.** Risk and parity suites
(`slot_risk_parity`, `backtest_live_parity`, `live_slot_book`, `multislot`, `money_sim`,
`exit_parity`, `live_exit_parity`): **101 passed.**

---

## What to do on the VPS

1. Pull and restart the bot.
2. The action default is now `CLOSE`. Your saved slot still says `CLOSE_AND_PAUSE` —
   change it to `CLOSE` on the Crash 1000 / Drift & Jump Alpha slot if you want it to
   bank and keep trading.
3. On the next scan cycle after restart you should see, for any open position:
   `[CB] adopted open group <id> (Crash 1000 Index) that this process did not open`.
   That line is the fix working. If a target is already met, the close follows in the
   same cycle and is logged under `RISK`.
