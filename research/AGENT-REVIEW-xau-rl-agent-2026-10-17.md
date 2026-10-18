# Review: xau-rl-agent (17 Oct 2026)

**Source:** `algoedge-agent-xau-rl-agent-2a115e02-2026-10-172123.json`, exported from the VPS at 21:23 UTC. Every number below comes from that file. Where I interpret a number, I say so.

## Verdict in one paragraph

The training ran **cleanly and correctly**, the first full run to do so:
- 50 of 50 jobs, 0 crashes;
- 14,974 of 15,000 iterations scoreable;
- 3,399 test trades;
- no data leakage found.

**But the agent does not have a proven edge, and it should not trade live.** The gate rejected it for five reasons, and the three most important say the same thing in different ways:
- the result is **very likely luck from trying 50 combinations** (deflated Sharpe: 0% chance it's real; PBO 0.70);
- it **doesn't survive higher costs** (−0.09R at 2× costs);
- **most of its result appears even when its inputs are shuffled** (+0.086R shuffled against +0.125R real).

It was approved anyway, which is now possible to do only knowingly (see section 6). The right next step is a **backtest on the 2½ months it has never seen** (1 Aug → today), compared with ORB and Trend Breakout on the same bars. That can now be done from the Backtester.

---

## 1. What was trained

| Setting | Value | Comment |
|---|---|---|
| Mode | A (finds its own entries) | Hardest mode to get a real result from |
| Symbol / timeframe | XAUUSD M5, with M15 and H4 context | |
| Data | **20 May 2025 → 31 Jul 2026**, 84,724 bars | The MT5 bar cap limits this (about 95k bars). Raising *Max bars in chart* to Unlimited gives years more |
| Iterations / seeds / paths | 300 × 5 seeds × 10 paths = 50 jobs | |
| Director (supervised model) | Off | |
| Stop / target menu | 0.75–3× ATR / 1–5R | The target choice now really reaches the trade (fixed 16 Oct) |
| Risk config used | **ORB_v1's slot** (1% risk, max 5 trades a day, 1 position) | The agent was judged under ORB's account limits, not limits of its own (see 4.3) |
| Server | 2 CPU cores, so 1 job at a time | **18.1 hours** for the run (21.7 min per job) |

## 2. Training: the good news

- **Every job finished:** 50 of 50, no crashes.
- **Nearly every iteration could be scored:** 14,974 of 15,000. Before 16 Oct's fixes this was often 0.
- **38 of 50 test folds made money.** Median test result **+0.15R per trade**, mean +0.17R, median Sharpe +1.23.
- **The five seeds broadly agree:** per-seed mean R +0.13, +0.16, +0.26, +0.15, +0.15. Dispersion 0.98, just under the 1.0 limit.
- **No leakage:** all 26 features are stable when future bars are added (leak probe PASS).
- **Not timing-sensitive:** entering one bar late keeps 104% of the result (PASS).

## 3. The gate: why it was rejected

| Check | Result | Bar | What it means |
|---|---|---|---|
| Sample size | 595 trades | 100 | ✅ enough trades |
| Seed dispersion | 0.98 | ≤ 1.0 | ✅ just inside |
| Regime split | +0.107 / +0.212 / **−0.233**R | 2 of 3 positive | ✅ passes, but **the third window loses** |
| Adherence | 0.81 | 0.6 | ✅ |
| **PBO** | **0.70** | ≤ 0.20 | ❌ 70% of the time, the fold that looked best in training was *below average* out of sample. Picking the "best" fold is mostly picking noise |
| **Deflated Sharpe** | Sharpe +1.09 vs **+4.84 expected from 50 attempts on noise**. P(real) = **0%** | > 0 | ❌ After accounting for 50 tries, the observed Sharpe is what pure noise would produce |
| **Cost stress 2×** | **−0.095R** | > 0 | ❌ At double the measured spread and slippage, it loses. The edge is thinner than the cost uncertainty on gold |
| **Exit head** | +0.125R with it, +0.125R without | > 0 | ❌ The learned exit adds **nothing**. It's extra complexity with no benefit |
| **Charter fidelity** | **28%** | ≥ 65% | ❌ The approved charter's six rules reproduce only 28% of what the agent actually does. The charter **does not describe the agent**, so live "adherence" monitoring would watch rules it doesn't follow |
| Shuffled labels | +0.086R (t +0.73) | — | ✅ "passes" because it isn't significant. But read the size: **features shuffled into nonsense still produce +0.086R of the +0.125R**. My reading: about 70% of the result comes from the trade structure (stops, targets, caps), not from the agent reading the market |

## 4. What the trade funnel shows

Over the test folds: **107,312 signals raised → 3,399 trades (3%).**

| Refusal | Count | Meaning |
|---|---|---|
| Daily max trades (5/5) | 43,418 | The agent wants to trade almost every bar; the daily cap picks the **first 5 of each day** |
| Same direction already open | 40,439 | It keeps re-signalling while already in a position |
| Max positions (1/1) | 13,786 | Same |
| Lot size below broker minimum | 5,929 | Risk shrank (drawdown brake) below 0.01 lot |
| Others | about 340 | Minimum stop, weekly drawdown, stale fills |

**4.1 It over-trades.** A policy that signals on most bars isn't selective. With three actions (buy, sell, flat), a `min_confidence` of 0.40 is barely above chance, which is 0.33.

**4.2 The caps do the choosing.** Which trades happen is decided by "first five of the day", not by the agent. Live would behave the same way, so this isn't a parity bug. But it means the agent's measured result is partly a result of ORB's caps.

**4.3 It was judged under ORB's risk settings** (the risk config came from the ORB_v1 slot). An agent should have its own slot risk, chosen for it.

## 5. The charter (what you approved)

- **Six gates, tier A means 5 of 6 must hold.** The strongest is "move over the last 5 bars ≥ −0.08 ATR", which fires on 55% of bars.
- Measured over only **85 trades** in the deterministic replay (the training folds had 3,399), at +0.43R each.
- **Fidelity 28%:** as in section 3, the rules don't capture the policy.

## 6. Changes made today because of this review

1. **Agents can now be selected on their own,** wherever a strategy can be:
   - the Backtester's *Strategy Engine* (single run);
   - each portfolio row;
   - the live **Trading Book** (Settings → Trading Book, both "Add slot" and each slot's picker).

   They appear under an **"Approved agents"** group, labelled "(failed gate)" when they failed. Before this, the pickers only listed the 12 built-in strategies, an agent could only be added as a *supervisor*, and the Backtester silently swapped an unknown id for ORB_v1. The backend always accepted agents; it was purely a UI gap.
2. **Compare panel** in the Backtester (single mode). Tick any mix of strategies and agents, for example *this agent + Range Breakout + Overnight Session + ORB + Trend Breakout*. They run **one after another on the same symbol, dates and risk**, and the results show side by side: trades, P&L, PF, win rate, expectancy, Sharpe, max drawdown, significance. The best is starred only among runs with 30+ trades.
3. **The slot editor no longer shows ORB's parameters for an agent.** It says the agent has no editable parameters; its risk tab still applies.
4. **Approve warns when the gate failed.** A red banner lists the failed checks, and the button reads "Approve despite failed gate" and asks for confirmation.
5. **`/strategy-defaults` was returning 404 on every server without the Strategy Factory switch.** The Trading Book and the slot editor silently lost the measured exits and the "no measured target" warning. It's now on an ungated, read-only route.
6. **Log rotation:** a newly spawned training worker briefly opened `backend.log` and could make a rotation fail. The shared log files now open only on first write.

## 7. What I recommend, in order

1. **Don't trade this agent live,** not even small. Every robustness check that would catch luck failed.
2. **Run the honest test, which is now one click away.** In the Backtester, pick *Agent: xau-rl-agent v1*, XAUUSD, dates **1 Aug 2026 → today** (never seen in training). Then in **Compare**, tick it with **ORB, Trend Breakout and Range Breakout**. If it can't beat Trend Breakout there (the strategy with a real, measured edge on CFDs, per the research notes), that settles it.
3. **If you retrain, change these settings:**
   - **More data:** set MT5 *Max bars in chart* to Unlimited first. 15 months of M5 is little for a policy with 26 inputs.
   - **`min_confidence` 0.55**, so it trades selectively instead of on every bar.
   - **Turn the exit head off:** it added nothing and is the most overfit-prone part.
   - **Its own risk slot,** not ORB's: a daily cap and position limits chosen for the agent.
   - **Consider mode B on Trend Breakout** instead of mode A. Improving a strategy that already has an edge is far easier than finding one from scratch, and the gate can then check "beats its base strategy".
   - **Fewer iterations (100–150):** 99.8% of iterations were already scoreable, so 300 mostly adds overfitting and hours.
4. **Speed on your VPS:** it has **2 CPU cores**, so training runs one job at a time (18 hours for this run). Options:
   - train on a machine with more cores (your 4-core PC halves it; 8 cores is about 4× faster);
   - accept the overnight runs;
   - use fewer paths and iterations.

   Settings: `RL_JOB_WORKERS` and `RL_MAX_AGENTS` in `.env`, with `RL_RESERVED_CORES=1` on a box that only trains.

## 8. "Create agent": settled

You confirmed it was a slip. Selecting an agent in place of a strategy is done (section 6).

---

# Update: 18 Oct 2026, what was built from your follow-up

Everything below is in the code on `dev`, uncommitted. Nothing is deployed to the VPS yet. Each item says how it was verified.

## 9. Mode B on several strategies at once

**What you asked:** one agent that trains on every strategy for a pair at the same time and picks the confluences that pay best, instead of one agent per strategy.

**What it does now.** In *New agent → mode B* the strategy picker is a set of checkboxes: tick one or several.
- Every ticked strategy runs on the same bars and finds its own setups. The agent is offered **every** setup from **any** of them.
- The agent gets **one extra input per strategy**: the direction of that strategy's most recent setup (+1 buy, −1 sell), fading to 0 over 24 bars (two hours on M5).
- It also gets one **"agreement"** input, the sum of those. So "ORB fired long 3 bars after Trend Breakout fired long" *looks different* to the agent from a lone ORB setup, and it can learn whether that combination is worth taking. Those are the confluences across strategies.
- The agent still can't reverse a strategy: it takes a setup only if it agrees with its direction. When two strategies fire on the same bar, it takes the first it agrees with.
- The **baseline it must beat** is all the ticked strategies traded side by side, untouched. The gate's *beats its base strategy* check uses that combined book.

**Parity.** The same logic now runs in the Backtester and live (section 12). Measured on the same bars:
- live found **72 of 72** of training's setups on the identical bars, from the same strategies;
- the confluence inputs agree to within 10⁻⁹.

**Several pairs in one agent: fixed, with one honest limit.** Before, one spec with several symbols trained each one separately, then kept a **single** best policy, with a scaler fitted to one symbol's prices, and used it on every pair: gold's weights trading EURUSD.

Now **each pair keeps its own best policy and scaler** inside the same agent:
- Collect stores them all.
- The Backtester and live trade each pair with its own.
- Distil and contributions use each pair's own policy.
- The policy hash covers every pair.
- Verified: a two-pair training run wrote two different policies and scalers, the registry round-trips them, and the live engine picks the right one per symbol.

The limits:
- **The pairs don't share what they learn.** It isn't one pooled brain: every pair still needs enough of its own bars.
- **The gate checks the first pair only,** and says so in its log. If gold is first, gold is what was gated. Treat the other pairs as ungated until you run them through the Backtester's Compare on unseen dates.

## 10. `min_confidence` default is now 0.55

Measured before changing it, because the code carried an older note: a 6-iteration run had taken **zero** trades at 0.55. Same everything except the floor: XAUUSD M5, 1 Jan → 31 Jul 2026 from your MT5, 40 iterations, 5 seeds × 8 paths = 40 runs each, 4 at a time.

| | 0.40 (old default) | **0.55 (new default)** |
|---|---|---|
| Runs finished / crashed | 40 / 0 | 40 / 0 |
| Runs with zero trades | 0 | **0** |
| Test trades | 1,618 | 2,090 |
| Median test R per trade | −0.011 | **+0.040** |
| Profitable runs | 19 of 40 | **22 of 40** |
| Scoreable iterations | 1,566 / 1,600 | 1,393 / 1,600 |

The zero-trade problem was a short-training artefact. At a real length 0.55 is better on every result line, so it's now the default in the form, the API and the spec.

Read it modestly: +0.04R median over 7 months of mode-A gold is still **not an edge** that would pass the gate. The default is better; it isn't proof of profit.

Two by-products of this run:
- It's the first parallel run since the worker-crash fix: **80 of 80 runs, 0 crashes**. Before the fix the same experiment lost 38 of 40.
- Trades went *up* at the higher floor. The policy learns to be confident where it trades, rather than the floor merely filtering a fixed policy.

## 11. The reward changes you asked for

**Exit calls (your rule, exactly).** Every early close by the exit head is replayed to see what would have happened had the trade been held:
- **+1 point** if the trade *would have hit its stop loss*: the exit saved a loser;
- **−1 point** if it *would not have*: the exit was unnecessary;
- 0 for trades that weren't closed early.

Each point is worth 0.30R in the money half of the reward. It's symmetric, and that's what stops it overfitting: closing everything early earns −1 on every trade that would have survived, so the head is only paid for real reversal calls. This replaces my earlier advice to switch the exit head off. Keep it on; it now has a direct signal for "this is going straight to SL". The Training tab's fold table now has an **Exit calls ✓/✗** column for each test fold.

**Money is proportional to profit.** The money term is the trade's realised R: linear, with no cap, so a +4R trade earns four times a +1R trade. At a fixed risk % per trade, R is money: at 1% risk, +1R = +1% of the account.

**70 / 30.** 70% money and 30% strategy adherence. Adherence is **signed** around the charter's floor: following the charter adds, ignoring it subtracts. Before, a plain 0–1 score meant ignoring the strategy cost nothing.

Verified: 57 reward tests, including new ones for both exit-call cases.

## 12. Training ≈ backtest ≈ live: what was wrong and is now fixed

You asked that what you get in training is what you get in backtest and live. Three things broke that:

1. **Mode B didn't exist outside training.** The engine used by the Backtester and live trading ran *every* agent as mode A: it applied the policy on every bar and ignored the base strategy completely. A mode-B agent was trained on strategy setups and then traded something else. **Fixed:**
   - the agent runs its base strategies *inside itself*, on the same bars, with the same saved settings;
   - it enters only on a setup it agrees with;
   - it feeds the same confluence inputs.
   This is the 72-of-72 match above.
2. **Mode B decided one bar late in training.** The setup was keyed to the bar *after* the strategy's decision, so the agent filled one bar after its base strategy would have. It isn't look-ahead, but it's the same one-bar-late error that cost GBPJPY 10R in September. **Fixed:** the decision bar is now the strategy's own.
3. **Parallel training could lose almost a whole run.** A Windows/Python 3.12 bug: any system-info (WMI) query inside a training worker invalidates the worker pool's own handle. The Telegram/TLS libraries make that query on import. One worker died, and **38 of 40 jobs failed** with "process pool terminated abruptly". **Fixed** at the cause: WMI is switched off in workers. The trainer also now rebuilds a broken pool and re-runs only the unfinished jobs.

One difference remains, and it's deliberate: training rollouts ignore the *daily* account brakes (max trades per day, daily/weekly drawdown), so the agent learns from its own decisions instead of "first five of the day". Test folds, the gate, the Backtester and live all apply the real limits.

## 13. Edit & retrain

There's now an **Edit & retrain** button next to *Clone* on every agent:
- It opens the **full creation form with every setting the agent used**: mode, strategies, director, risk menu, features, iterations, CPCV, reward weights, min confidence. Everything is editable.
- The Data tab's sources and date window come along too.
- It's saved as a new agent named `<name>-r2`, then `-r3`, and so on. The old agent, its charter and any live slot pointing at it are untouched. (A live slot refers to an agent as `RL::<name>@<version>`, so a retrain can't reuse the name.)
- Weights aren't carried over; training starts fresh.

*Clone* still exists for "same experiment, different symbols".

## 14. What each indicator contributes

There's a new **"What each input contributes"** panel on the agent's Overview, and the result is in the export too. Press *Measure contributions*. For every input (the EMAs, ATR ratios, the director's call, each strategy's setup flag) it reports:
- **R contribution:** the input is shuffled across bars (real values, link to the market cut), and the whole run is replayed through the real engine with costs and limits. The R lost is what that input was worth. **Negative means the agent does better without it,** a candidate to drop on the next retrain.
- **Δ P(trade)** and **Δ long vs short:** how much the decision moves when the input rises by one standard deviation.

Give it a *From* date after the training window to see what still pays, rather than what the agent learned to lean on. It takes one engine replay per input: a few minutes on 20,000 bars.

## 15. What to expect: results and returns

I won't give you a return figure that the measurements don't support. Here is what each change should do and how you'll know.

- **The xau-rl-agent you have (mode A, approved despite the gate) is unchanged by any of this.** Its expected live return is still about zero after costs, because it failed every luck check. The forward backtest (1 Aug → today) is still the deciding test.
- **The changes improve the *honesty* of the next agent more than its headline number.** Mode-B parity means a mode-B backtest now trades what was trained. Before, it traded something else, so any earlier mode-B backtest number was meaningless. Expect the next agent's backtest to be **closer to its test-fold result**, in either direction.
- **min_confidence 0.55:** see section 10 for the measured effect on trade count and R.
- **Exit-call reward:** expect the exit head to close *fewer* trades early, and the ones it closes to be stop-bound. Watch the **Exit calls ✓/✗** column in the Training tab's fold table. If right ≤ wrong after training, the head has no skill: switch it off for that agent.
- **Multi-strategy mode B on gold** is the best chance of a real edge in this setup. The bar to clear is concrete: the gate's *beats its base strategies* check against the combined ORB + Trend Breakout + Range Breakout book on the same bars. A realistic good outcome is **+0.05 to +0.15R per trade above that book**, which at 1% risk and 2–4 trades a day is roughly **+1% to +5% a month on top of what the strategies already make**. I'll stand behind that only once the gate passes it on unseen bars. Anything far above it on the test folds is more likely overfit than real; this project's history says so repeatedly.

## 16. My take: reinforcement only, or supervised + reinforcement, for gold over several strategies?

**Short answer: mode B over several strategies, *with* the supervised director. Not mode A.**

**Why mode B beats mode A** (finding its own entries):
- Mode A has to learn direction, timing and selection at once from about 85,000 noisy M5 bars. This review shows the result: most of its edge survived shuffling its inputs, so it was mostly trading structure, not reading gold.
- Mode B starts from setups that already have a measured edge (ORB and Trend Breakout on gold, per the research notes). The agent only learns **which** setups and **which combinations** to take, plus the stop, target and exit. That is a much smaller problem, so the same data goes much further.
- It also comes with a baseline it must beat, so you'll *know* whether it added anything.

**Why add the supervised director.** Supervised learning and reinforcement learning answer different questions:
- The director (a logistic model, fitted first and then frozen) answers **"which way is gold likely to go over the next N bars"**. Labelled data answers that question efficiently: every bar is a training example.
- RL answers **"is this setup worth taking given that call, how much do I risk, and when do I get out"**. That depends on costs, stops and exits, which labels can't teach. And RL only gets one noisy reward per *trade*.

Using RL alone makes it learn direction from a few thousand trade rewards. With the director, it gets the direction from tens of thousands of labels and spends its few rewards on the part only it can learn.

On multi-strategy mode B, the director's call becomes one more confluence. "ORB long + Trend Breakout long + director long at 0.62" is exactly the combination the agent can learn to prefer.

**When RL-only would be better:** if the director turns out useless. The contributions panel will show this: shuffle `director_call` and nothing is lost. Then it's two extra inputs of noise, and the leaner RL-only agent overfits less. So train **both** on the same gold bars (*Edit & retrain* makes the second a toggle) and let *beats its base strategies* plus the forward backtest pick the winner. Don't pick in advance.

**Suggested first run:**
- XAUUSD M5, mode B on **ORB_v1 + TrendBreakout_v1 + RangeBreakout_v1**, director on, horizon 12;
- min_confidence 0.55, exit head on (new reward), 100–150 iterations;
- MT5 max bars Unlimited first;
- its own risk slot.

Then run the same spec with the director off, and compare.
