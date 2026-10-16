# Setup guide: accounts, keys and the build plan for the two new systems

*16 Oct 2026. This covers the **meme-coin scam filter and trading bot** (from the TikTok screenshots) and the **Hyperliquid market maker** (Video 3). The research behind both is in `research/memecoin/PLAN-2026-10-16.md` and `research/market-making/PLAN-2026-10-16.md`. This file is the practical part: what you need to get, how to get it, and what I'll build.*

> **Two rules for every key in this guide**
> 1. You create every account and key yourself. You put them on the server yourself, in the `.env` file, which never goes into git.
> 2. I never need to see a private key or a password. Wallet private keys in particular are **never** pasted into chat, a document, or the code.

---

## Part 1: What to get, in order

You don't need everything at once. Get the **Stage 1** items and I can start. Get the others when the plan reaches their stage.

| Stage | Item | Needed for | Monthly cost (check current prices) |
|---|---|---|---|
| **1** | Linux VPS in Europe (meme bot) | Collector running 24/7 | $10–30 |
| **1** | Helius API key | Solana data | Free tier to start; about $50 for the paid tier |
| **1** | xAI (Grok) API key with a spend cap | X/Twitter checks | Usage-based; cap it at $20–50 to start |
| 2 | Birdeye or Bitquery key *(optional)* | Historical backfill, so we don't wait 2 weeks | About $100, for one month |
| 3 | Burner Solana wallet + $50–100 of SOL | Tiny live meme test | Test capital only |
| **1 (MM)** | Hyperliquid account (a wallet) | Market-making recording, then trading | Free |
| **1 (MM)** | Linux VPS in Tokyo | Low latency to Hyperliquid | $10–40 |
| 2 (MM) | Tardis.dev *(optional)* | Historical order-book data | Varies; one month is enough |
| 4 (MM) | Hyperliquid API wallet + $200–500 USDC | Tiny live market-making test | Test capital only |

### 1.1 Linux VPS for the meme bot (Europe)

**Why a new server:** the current Windows VPS runs MT5 and the live AlgoEdge bot. A collector that handles over 1,000 tokens an hour shouldn't compete with that, and it should sit near Solana's validators, which are clustered in Europe and the US.

1. Sign up at a provider such as **Hetzner** (Falkenstein or Helsinki), **DigitalOcean** (Frankfurt or Amsterdam), or **Vultr** (Frankfurt or Amsterdam).
2. Create a server: **Ubuntu 24.04 LTS, 2 vCPU, 4 GB RAM, 80 GB disk**.
3. Choose **SSH key** login, not a password. On your PC run `ssh-keygen -t ed25519` and paste the `.pub` file's contents into the provider's form.
4. Note the server's IP address. Send me the IP and the username (usually `root`), never the private key. Then either:
   - give me shell access the way you prefer, or
   - run the setup script I'll write for you.
5. In the provider's firewall, allow only port 22 (SSH) from your own IP, plus 443 if you want the dashboard reachable.

### 1.2 Helius (Solana data)

**Why:** the public Solana RPC is rate-limited and drops websocket connections. Helius gives a reliable feed of new pools, holder data and transaction history.

1. Go to **dashboard.helius.dev** and sign up (email, GitHub or a wallet).
2. Create a project. The dashboard shows an **API key**, an **RPC URL** (`https://mainnet.helius-rpc.com/?api-key=…`) and a **websocket URL** (`wss://…`).
3. Start on the free tier. Move to the paid developer tier when Stage 1 collection begins, because the free credits run out quickly at about 1,250 tokens an hour.
4. Put these on the meme VPS in `.env`:
   ```
   HELIUS_API_KEY=...
   HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?api-key=...
   HELIUS_WS_URL=wss://mainnet.helius-rpc.com/?api-key=...
   ```

### 1.3 xAI (Grok) API key

**Why:** Grok's one unique capability here is live search over X (Twitter). The SOCIAL check needs it: whether the token's X account is real, its age, whether it was renamed or recycled, and whether the attention is bots. We've never used Grok in AlgoEdge.

1. Go to **console.x.ai** and sign in with an X account or email.
2. Open **Billing**. Add a payment method or prepaid credits, and **set a monthly spending limit** ($20–50 is enough for the pilot).
3. Open **API Keys**, then **Create API key**. Name it `memebot`, and if the console offers it, restrict it to the chat/completions endpoints.
4. Copy the key once, then put it in the meme VPS's `.env` as `XAI_API_KEY=...`.
5. Tell me which models your account offers. The console lists them; I'll choose the cheapest one that supports X search.

### 1.4 Optional: Birdeye or Bitquery (historical backfill)

**Why:** free APIs only list recent tokens. A paid source lets us backtest on 3–6 months of launches straight away, instead of waiting 2 weeks for our own data.

- **Birdeye:** go to **bds.birdeye.so**, sign up, choose the plan that includes historical OHLCV and the token-creation lists, then copy the API key into `.env` as `BIRDEYE_API_KEY=...`.
- **Bitquery** (alternative): go to **bitquery.io**, sign up, create an API key, then copy it into `.env` as `BITQUERY_API_KEY=...`.

One month is enough. Cancel afterwards.

### 1.5 Burner Solana wallet (Stage 3 only, after paper trading passes)

1. Install **Phantom** or **Solflare** in a browser profile you use for nothing else.
2. Create a **new** wallet. Don't reuse a wallet that holds real savings. Write the recovery phrase on paper.
3. Fund it with **$50–100 of SOL** from an exchange.
4. Export the wallet's private key (Settings → Security → Export Private Key). Then **you** paste it into the meme VPS's `.env` as `SOLANA_BURNER_KEY=...`, directly on the server.
5. Never send it to me. The bot reads it from the server; I only ever see the wallet's public address.

### 1.6 Hyperliquid account (Video 3: market making)

1. Install **Rabby** or **MetaMask**, and create a **new** wallet for this.
2. Go to **app.hyperliquid.xyz** and **Connect** that wallet.
3. **Stage 1 (recording only)** needs no deposit and no key. The order-book data is public.
4. **Stage 4 (tiny live test):**
   1. Deposit **$200–500 USDC** (on Arbitrum) through the app's Deposit button.
   2. Open the **API** page (under *More*). Create an **API wallet**, give it a name, and **authorize** it. An API wallet can place and cancel orders but **cannot withdraw**.
   3. Copy the API wallet's private key **once**, straight into the Tokyo VPS's `.env`:
      ```
      HL_ACCOUNT_ADDRESS=0x...   (your main wallet's public address)
      HL_API_WALLET_KEY=...      (the API wallet's private key)
      ```
5. Check that trading perpetuals on Hyperliquid is legal where you live before Stage 4.

### 1.7 Tokyo VPS (market maker)

**Why:** Hyperliquid's API is served from AWS Tokyo. From your PC a round trip took about 550 ms (measured); from Tokyo it should be single-digit milliseconds.

1. Rent a **small Linux VPS in Tokyo**: **AWS Lightsail** (region ap-northeast-1), **Vultr Tokyo**, or similar. Ubuntu 24.04, 2 vCPU, 2–4 GB RAM.
2. Same SSH-key setup as section 1.1.
3. Before we commit to it, I'll measure the actual latency from it to the Hyperliquid API.

### 1.8 Optional: Tardis.dev (historical order books)

1. Go to **tardis.dev**, sign up, choose a plan that covers Hyperliquid, and copy the API key into the Tokyo VPS's `.env` as `TARDIS_API_KEY=...`.
2. The alternative is to wait two weeks while our own recorder builds the data. That is free.

### 1.9 Already set up, keep using

- **Telegram bot:** both new services send their alerts and daily summaries to the same chat as AlgoEdge. No new bot is needed.
- **Anthropic key:** already on the AlgoEdge server. The meme bot's judge can use Claude Haiku instead of Grok wherever X search isn't needed, which is cheaper.

### 1.10 For AlgoEdge itself (from today's fixes)

1. **MT5 bar limit:** in MT5, open *Tools → Options → Charts → Max bars in chart* and set it to **Unlimited**, then restart MT5. Without this, training data is capped at about 95,000 bars, counted back from today.
2. **HTTPS for the admin panel:** use `https://admin.alphavantiqcapital.com`, and in the VPS firewall close ports 80 and 8000 on the bare IP `16.60.51.87`. Right now the admin login crosses the network unencrypted.
3. **Before the next deploy:** stop pm2, rename the stuck `logs\trades.log`, deploy, then start pm2.
4. **Optional training settings** in `.env`: `RL_MAX_AGENTS=1` and `RL_JOB_WORKERS=2` on a 4-core VPS (see section 4 of the report).

---

## Part 2: What I'll build

### 2.1 Where things run

```
                         ┌────────────────────────────────────────────┐
  you (browser) ──https──▶  Windows VPS  (existing)                   │
                         │   AlgoEdge API + admin + investor sites    │
                         │   MT5 + live CFD bot                       │
                         │   new admin tabs: "Meme Lab", "MM Lab"     │──┐ read-only
                         └────────────────────────────────────────────┘  │ API calls
                                                                          │
   ┌──────────────────────────────────────────┐   ┌──────────────────────▼───────────────┐
   │ Linux VPS, Europe: memebot               │   │ Linux VPS, Tokyo: mm                  │
   │  detector (Helius ws + GeckoTerminal)    │   │  recorder (L2 + trades websocket)     │
   │  hard filter (RugCheck + RPC + sell sim) │   │  fair value + quoting engine          │
   │  enricher, Grok SOCIAL check, judge      │   │  risk + kill switches                 │
   │  risk, executor (paper / live)           │   │  executor (post-only, paper / live)   │
   │  Postgres + small read-only API          │   │  Parquet recordings + read-only API   │
   └──────────────────────────────────────────┘   └───────────────────────────────────────┘
                         all three → the same Telegram chat
```

**Why split this way:**
- **Each system next to its own market:** Solana validators for the meme bot, Hyperliquid's servers for the market maker.
- **A crash in one can't touch the others,** including your live CFD bot.
- **One admin panel and one login.** The new services only expose read-only APIs, and the existing admin panel shows them. Nothing new faces the internet.

### 2.2 Backend: memebot

- **Language and stack:** Python 3.12, asyncio, `httpx`, `websockets`, `solders`/`solana-py`, Postgres (SQLite while piloting), and `pandas`/`numpy` for the replay.
- **Modules:**
  - `detector`: subscribes to new-pool events over the Helius websocket, with GeckoTerminal polling as a backup. **A token's clock starts at its first real trade, not at creation.** Today's pilot showed most tokens never trade.
  - `hardfilter`: mint and freeze authority, LP lock, top-10 holders, creator history, and a **sell simulation**, which catches honeypots. Every rejection is stored with its reason.
  - `enricher`: holder distribution over time, creator wallet history, liquidity and momentum at 1/5/15/60 minutes.
  - `social`: the Grok X-search check, on survivors only, about 5–10% of tokens. Stores the answer, the reasoning and the cost.
  - `judge` ("Jev"): starts as transparent rules, then a small classifier trained on our own collected outcomes. It is optionally paired with a cheap LLM. All three versions are benchmarked against each other.
  - `replay`: the backtester. It uses AMM and bonding-curve price impact on real reserves, fees, priority and Jito costs, 1–3 block delay, and failed-transaction and honeypot losses. It compares the system against random-entry controls.
  - `risk`: fixed size, maximum open positions, daily loss cap, a kill switch, and an automatic exit when liquidity is pulled.
  - `executor`: paper mode by default. Live mode goes through Jupiter or pump.fun, with Jito bundles.
  - `api`: read-only (FastAPI) endpoints for the feed, rejections, survivors, positions, P&L and costs.
- **Speed:** websocket ingestion, everything async, cheap filters before expensive ones, and Grok only on survivors.

### 2.3 Backend: mm (market maker)

- **Language and stack:** Python 3.12 asyncio, using the Hyperliquid websocket and REST API, Parquet recordings, and `numpy`. It moves to Rust only if latency ever becomes the limit.
- **Modules:**
  - `recorder`: every L2 book update and every trade, timestamped, written to Parquet.
  - `stats`: spread, depth, fill probability against distance from the touch (k), volatility (σ), and adverse selection after fills.
  - `model`: microprice fair value and Avellaneda–Stoikov quotes with inventory skew, plus toxicity filters.
  - `sim`: an event-driven simulator with a queue-position model, fees and latency.
  - `risk`: inventory caps, loss limits per hour and per day, and a stale-data kill switch.
  - `executor`: post-only orders and cancel/replace within the rate-limit budget. Paper first.
  - `api`: read-only endpoints for P&L **split** into spread earned, fees, adverse selection and inventory.

### 2.4 Frontend: two new tabs in the existing admin panel

- **Meme Lab:**
  - live feed of new tokens with pass/reject and the reason;
  - funnel chart (seen → passed filter → passed social → judged yes → traded);
  - open positions;
  - paper/live P&L against the random-entry control;
  - Grok cost per day;
  - a kill switch.
- **MM Lab:**
  - per-market spread and depth;
  - inventory gauge;
  - P&L attribution chart;
  - fills;
  - toxicity indicator;
  - a kill switch.
- **Shared:** the same React app, components and login. The data comes through the AlgoEdge API, which forwards to the services, so the browser never talks to them directly.

### 2.5 "Optimal, fast and profitable": what I can and can't promise

- **Optimal and fast, I can engineer:** the right server locations, websockets instead of polling, cheap filters first, async throughout, and costs measured rather than assumed.
- **Profitable can't be promised by anyone,** and an honest build doesn't pretend otherwise. What I can promise is that **you'll know whether it is, before real money is at risk.** Every stage has a pass/fail bar set in advance. Live trading starts only if paper trading has beaten a random-entry control, after realistic costs, with a significant result. If the answer is no, you find out for the price of a VPS and a few weeks, not your capital. That's the same standard as AlgoEdge, where it has already saved you from several strategies that looked good and weren't.

---

## Part 3: Step-by-step plan, and why each step comes where it does

> **Updated 16 Oct (night).** Part 5 adds the strategies, filters, capital rules and LLM placement from your research brief. **The revised build order is in section 5.9 and replaces this table where they differ.** The main change: the Hyperliquid **funding carry** comes first, because it needs no speed advantage and was measured live today.

| # | Step | Takes | Needs from you | Why this order |
|---|---|---|---|---|
| 1 | Set up the meme VPS and **start collecting** (no trading) | 1–2 days to build, then 7–14 days of collection | 1.1, 1.2, 1.3 | Nothing can be tested until the data exists. Collection runs while I build everything else |
| 2 | Set up the Tokyo VPS and **start recording** Hyperliquid books | 1 day to build, then 14 days of recording | 1.6 (account only), 1.7 | Same reason. It's free data, and the recorder is the simplest piece |
| 3 | Build the replay backtester and the admin tabs (read-only) | 1–2 weeks, alongside 1 and 2 | — | Ready the moment the data is |
| 4 | Meme backtest: full system against random-entry controls | 2–3 days | (1.4 makes it earlier) | **First go/no-go.** If the filter and judge don't beat random selection after costs, we stop here |
| 5 | MM measurement: is spread minus fees minus adverse selection positive anywhere? | 2–3 days | — | **First go/no-go for MM.** If it's negative everywhere, we stop, having spent only the VPS cost |
| 6 | Paper trading for whichever passed | 2 weeks | — | Checks latency, failed transactions and fills against what the backtest assumed |
| 7 | Tiny live test for whichever passed paper | 2–4 weeks | 1.5 or 1.6 (API wallet + USDC) | Measures real fills against paper, with capital you can afford to lose |
| 8 | Scale up slowly, only if live matches paper | Ongoing | Your decision | Each size increase is a separate decision |

**Total before any real money:** about 4–6 weeks, mostly waiting for data, which runs in the background.

---

## Part 4: Checklist (send me these to start)

- [ ] Meme VPS created (Ubuntu 24.04, Europe). Send me the IP and how you want to give access.
- [ ] Helius account created, and its keys in that VPS's `.env`. Tell me when it's done.
- [ ] xAI account with a spend cap, and its key in that VPS's `.env`. Tell me which models it lists.
- [ ] Decision: pay for one month of Birdeye or Bitquery, or wait 2 weeks for collected data.
- [ ] Hyperliquid wallet created (no deposit yet). Send me its **public** address.
- [ ] Tokyo VPS created. Send me the IP.
- [ ] MT5 *Max bars in chart* set to Unlimited on the Windows VPS.
- [ ] Admin panel moved to HTTPS, and ports 80/8000 closed on the bare IP.
- [ ] Confirmed that meme-coin trading and Hyperliquid perpetuals are legal for you.
- [ ] *(Added in Part 5)* Decide where profits are swept to (section 5.7): a cold wallet you control, or back into the AlgoEdge broker account.
- [ ] *(Added in Part 5)* Optional: a **Dune** API key, so wallet histories can be vetted for copy trading (section 5.2), and a **Birdeye** key with wallet-PnL access.

---

## Part 5: How money is made in these markets, the filters, and how the bots work together

*Added 16 Oct (night) from your research brief. **How to read the numbers:** the brief's statistics (74% of buyers losing, $29.7M taken by sandwich bots, 43% of tokens rugging, and so on) are quoted from your brief and **I haven't checked them independently.** They match what's widely reported, and they are treated here as starting assumptions to measure, not as facts. Numbers marked **measured** come from this machine today.*

### 5.1 The one sentence that shapes everything

**In these markets the average participant loses after costs.** The money goes to the people with a structural advantage: faster, better informed, better capitalised, or better at risk control. So the plan doesn't ask "which strategy prints money". It asks **"where do we have, or can we build, one of those four advantages, and how do we check it before trading real size?"**

| Advantage | Can we get it? | Where it applies |
|---|---|---|
| **Speed** | Partly. A Tokyo VPS gets about 2–3 ms to Hyperliquid (the brief) against the **~550 ms measured** from your PC. Solana speed is a race against professional snipers we won't win outright | Market making; reacting first to Hyperliquid liquidations |
| **Information** | Yes, narrowly. Filtering, wallet analytics and the X/Twitter check (Grok) can know more about a token than a fast buyer does | Meme-coin selection; copy trading |
| **Capital** | No, not at first | — |
| **Risk discipline** | **Yes, fully.** This is the one advantage that can be engineered completely, and it's what AlgoEdge already does well | Everything |

So the ranking below favours strategies where **information and discipline** matter more than raw speed.

### 5.2 Meme coins (Solana): ways to make money, ranked by what we can realistically win

| Rank | Approach | Main risk | Why this rank | How we'll test it |
|---|---|---|---|---|
| **1** | **Filtered momentum after the first trades.** Don't snipe at launch. Wait until a token has *survived* its first minutes, passed every filter, and shows real buyers, then ride the move with tiny sizes across many tokens | Late entry; slippage | Rewards **information** (the filters), not speed. Most tokens die in their first minutes, and waiting skips most rugs | Phase 1 data replay: compare against random entry *at the same moment* on filter-passing tokens |
| **2** | **Copy trading vetted wallets.** Mirror wallets with a proven, *statistically shrunk* (see 5.6) multi-week realised profit, with size caps, and exit automatically when they sell | Copied wallets dumping on followers; short-lived luck | Information edge, but most "profitable" wallets are luck or manipulation, so vetting does all the work | Wallet histories from Dune/Birdeye. Pick wallets on months 1–2, test on month 3, then compare against randomly chosen wallets with the same activity |
| 3 | **Developer tracking.** Find deployers whose past launches did well, and enter their next one early | Deployers rotating wallets; this is the scammers' own playbook | A narrow, real information edge, but small in number and easily gamed | A sub-test of rank 1: does "creator's past outcomes" add value as a filter? |
| 4 | Sniping at launch or at pump.fun graduation | Losing the speed race; sandwich bots | Pure speed, competing against professional snipers. The brief's figure (first-minute buys profitable ~34% of the time) only works out if the winners are very large | Only if ranks 1–3 fail, and only as a measured comparison, never as the default |
| — | Influencer/narrative signals | They lag on-chain action | Used as **filter inputs** (Grok), not as triggers | — |

### 5.3 The full filter list (meme coins)

Filters run **cheapest first**. A token stops at the first hard reject, and every reason is stored, so we can later measure what each filter cost or saved.

**A. Hard rejects on the contract, when the pool appears (RPC + RugCheck, milliseconds, free):**
1. **Mint authority** not revoked: the creator can print more tokens.
2. **Freeze authority** set: the creator can freeze your tokens.
3. **Token-2022 extensions** (*new*): reject a **transfer fee** above 0, a **permanent delegate** (the creator can move your tokens), a **transfer hook** (custom code on every transfer), or a non-transferable or default-frozen account state.
4. **Metadata update authority** still held (*new*): the name and links can be changed after launch, which is used in impersonation.
5. **LP not burned or locked**, or a lock that ends within days (*new: check the unlock date, not just "locked"*).
6. **Sell simulation fails**: a honeypot. Simulate a sell of a small amount before every buy.

**B. Hard rejects on the holders, in the first seconds and minutes (RPC + Helius):**
7. **Top-10 holders own more than ~30%**, excluding the pool and burn addresses.
8. **Single holder above ~15%.**
9. **Bundled launch** (*new*): several wallets buying in the **same block as creation**, funded from the same source. This is the most common way creators disguise a large holding.
10. **Sniper concentration** (*new*): the share of supply bought in the first 1–3 blocks by known sniper wallets. When it's high, they dump on later buyers.
11. **Creator funding source** (*new*): creator wallet funded minutes before launch through a mixer or a fresh chain of wallets, versus an old, known wallet.
12. **Creator history**: past tokens rugged, or a high rate of abandoned launches. **Measured today:** 24 of 87 new tokens were flagged for this.

**C. Soft scores on activity, while a token is alive (computed each minute; these feed the judge rather than rejecting):**
13. **Real-buyer ratio** (*new*): unique buyers ÷ transactions. Low means wash trading.
14. **Trade-size distribution** (*new*): many equal-sized trades are bots. Real crowds look irregular.
15. **Buy/sell imbalance** and its trend.
16. **Holder growth rate**, against **holder churn** (wallets selling out entirely).
17. **Liquidity depth against our size** (*new, and decisive*): skip any trade whose expected price impact is over ~2%. **Measured today: median pool liquidity was $2,379**, so even $100 moves the price about 4%.
18. **Sandwich risk** (*new*): how often recent trades in this pool were sandwiched. Bad pools are avoided entirely, and all trades go through Jito bundles / MEV-protected sending.
19. **Momentum already spent**: price already up more than a set multiple since first trade means entering late into someone else's exit.
20. **Bonding-curve progress** (pump.fun): tokens close to graduating behave differently from early-curve ones; this is measured per bucket.
21. **Copycat name** (*new*): the name or ticker is very similar to a token that's trending right now, a common impersonation trick.

**D. Social check, on survivors only (Grok, about a second, costs money):**
22. Does the X account exist, how old is it, has it been renamed or recycled, and are its followers real?
23. **Pre-promotion accumulation** (*new*): did wallets buy just before an influencer posted? That's the influencer selling to their audience.
24. The website domain's age and whether its links work (*new*, cheap and cached).

**E. Market context, applied to every token (*new*):**
25. **SOL's own trend and volatility.** Meme coins lose together when SOL drops, so size shrinks or trading pauses in SOL drawdowns.
26. **Network congestion and priority fees.** When fees spike, cost per trade jumps and transactions fail more often.
27. **Time of day.** Launch quality and competition vary by hour; this is measured, not assumed.

**F. Exit triggers, checked continuously on every open position:**
- the **creator sells**, or a top-10 holder sells more than X% of their stake;
- **liquidity drops** more than Y% (the start of a rug);
- volume decays below its entry level;
- trailing stop and maximum hold time;
- the daily loss cap is hit.

**How we keep this honest:** with 27 filters, some will look good by pure chance. Every filter is tested out of sample, and kept only if removing it makes results **worse** on data it was never tuned on. The same "what did this gate cost" method AlgoEdge already uses (the gate-cost table in the Journal's Learning Data view) applies here.

### 5.4 Hyperliquid: ways to make money, ranked

**Measured live today** (16 Oct, public API): 178 perpetual markets have volume, and 35 have at least $5M a day. The median funding rate among those 35 is **+11.0% a year**, which is Hyperliquid's baseline rate. **BTC** is +11.0% (open interest $3.37B), **ETH** −0.6%, **SOL −16.0%**, ENA −19.6%. *This is one snapshot; funding changes every hour.*

| Rank | Approach | Main risk | Needs a speed edge? | Why this rank |
|---|---|---|---|---|
| **1** | **Funding carry (hedged).** When funding is positive, hold the asset on Hyperliquid's spot market and short the same size of perpetual. Price moves cancel out, and the short collects funding every hour | Funding turning negative; the hedge legs drifting; exchange risk | **No** | Earns the baseline ~11% a year *before* fees, plus the spikes above it. It's a known structural payment, not a prediction. **Fees decide it:** entering and exiting both legs costs about 0.2–0.3% in taker fees (check current rates), so positions are held for weeks, not hours |
| **2** | **HLP vault (passive).** Deposit USDC into Hyperliquid's own market-making vault, which earns from market making and liquidations | Vault drawdowns during market crashes; a lockup on withdrawals (check current terms) | No | The simplest possible way to share in the venue's market-making profit. It's the **benchmark**: anything we build must beat just holding HLP |
| 3 | **Market making on long-tail markets** (the original Video 3 plan) | Toxic flow; inventory risk | **Yes** (Tokyo) | The spread exists there (5.6 bps measured on WIF), but it's paid precisely because the flow is toxic. This is where the most engineering goes, for the most uncertain result |
| 4 | **Momentum / trend on liquid perps, aware of funding.** Trend-following on BTC/ETH/SOL with a funding filter: don't go long when you'd pay a lot of funding | Leverage; liquidation | No | AlgoEdge already has trend systems that held up when tested properly; this ports the best one to a new venue |
| 5 | **Fading liquidation cascades.** After a cluster of forced liquidations knocks the price down, buy the overshoot | Catching a falling knife | Partly | Hyperliquid publishes positions and liquidation levels on-chain, which is a real information advantage here, but cascades sometimes keep going |
| — | Spoofing / fake orders | — | — | **Illegal.** Not built (see the Video 3 plan) |

**Filters and risk rules for every Hyperliquid strategy:**
- **Leverage cap:** 3× at most, whatever the venue allows (up to 40× on BTC). Most account blow-ups in the brief come from oversized positions.
- **Liquidation distance check:** never open a position whose liquidation price is within N daily moves (ATR) of the entry.
- **Stops held on the exchange,** not just in our bot, so they still fire if the bot crashes.
- **Funding-cost filter:** for directional trades, skip entries where expected funding cost over the hold eats more than a set share of the target.
- **Open-interest and funding regime filter:** open interest climbing fast while funding spikes is a crowded trade, so size shrinks.
- **Stale-data kill switch:** no order-book update for N ms means cancel all orders.
- **Daily loss halt and maximum open notional per strategy.**
- **Prefer maker orders:** a limit order that rests on the book pays 0.015% instead of the taker 0.045%, at base tier.

### 5.5 Where LLMs help, where they don't, and the speed budget

**The rule:** an LLM never sits on the fast path. A call takes 0.5–5 seconds and its answer isn't perfectly repeatable. Both are fine for research and labelling, and unacceptable for deciding a trade in milliseconds.

| Path | Speed budget | What runs there |
|---|---|---|
| **Hot** (entry and exit decisions) | under 50 ms in our code, plus the chain's own time | Fixed rules, cached values, and a small **trained classifier** (the "Jev" judge), distilled from what the slow path learned |
| **Warm** (per token, in the background) | 1–5 s, results cached | **Grok** for the X/Twitter check; cheap LLM labels on token descriptions and websites; computed once per token or creator, then reused |
| **Cold** (daily) | minutes | LLM post-trade reviews ("why did these 20 trades lose?"); summaries of research; proposals for rule changes, which **you** approve (the same charter approval flow AlgoEdge uses) |

**Using statistics and data well:**
- The hot-path classifier is trained on our own collected outcomes, and retrained weekly on a rolling window, *never* on the period it's then judged on.
- LLM answers are treated as features with a measured value, not as authority. If Grok's "this account is fake" doesn't improve results out of sample, it gets switched off and we stop paying for it.
- **Cost guard:** a daily budget for each provider, enforced at the point of spending. AlgoEdge's `llm_budget` pattern is reused.

### 5.6 The statistics toolkit (so we don't fool ourselves)

- **Shrunk win rates for wallet vetting.** A wallet that won 9 of 10 trades is not a 90% wallet. Its record is pulled toward the average of all wallets (a "Bayesian" estimate) before we trust it. Most 90% wallets turn out to be about 55%.
- **Deflated Sharpe and multiple-testing control.** Testing 27 filters and hundreds of wallets guarantees some false winners. The bar rises with the number of things tried, as in AlgoEdge's gate.
- **Survival analysis for tokens.** We model the *hazard* of a rug minute by minute after launch, rather than one yes/no label. This tells us how long to wait before entering is worth it (the entry rule for rank 1).
- **Out-of-sample by time.** We always train on earlier weeks and test on later weeks. A random train/test split leaks the future in markets.
- **Profit attribution.** Every trade's result is split into its causes: the filter, the entry timing, the exit rule, fees and slippage. That way we know *which part* makes or loses money.
- **Regime detection.** We track SOL and BTC trend and volatility states. Strategies get turned down in the regimes where they've measurably lost.

### 5.7 Capital: how much to use, and getting profits out quickly

This is the part of your brief about using the money made, and getting funds out of the market fast.

**Allocation (which bot gets how much):**
1. Every bot starts at a **fixed tiny allocation** for its live test ($50–100 for meme coins, $200–500 for Hyperliquid).
2. It earns more only by beating its benchmark live for a set period: random entry for meme coins, holding HLP for Hyperliquid.
3. Scaling follows **fractional Kelly with a hard cap.** Size grows with the measured edge and shrinks with its uncertainty, never above a fixed share of the total.
4. A bot that falls below its benchmark for a set period is **cut back automatically**. If its drawdown passes its limit, it's stopped and its capital is returned to the pool.

**Profit sweeping (getting money out of the market):**
- **Daily sweep.** Realised profit above each bot's working balance is moved out every day, automatically:
  - meme bot → a **cold wallet you control**. On Solana a transfer takes seconds.
  - Hyperliquid → withdrawn to your wallet on Arbitrum. Typically a few minutes, for a small fixed fee (check current terms).
- **The hot wallets only ever hold working capital.** If a key is stolen or a venue fails, the loss is limited to that working balance, never your accumulated profits. This is the most important protection in the whole plan.
- **Where swept profits go is your decision.** The options are your cold storage, or funding the AlgoEdge broker accounts. Moving money between your own accounts is something you do or approve; the bots only ever send to destinations you configure.
- **Exposure time as a measure.** Each strategy reports how long, on average, its capital is exposed to the market per unit of profit. Of two strategies earning the same, the one that returns its capital faster is preferred.

**Accounting:** every trade, fee and sweep is logged for tax purposes. Short-term gains are taxed heavily in many countries, so check what applies to you.

### 5.8 Security, the risk that the brief calls common

- Trade-only keys everywhere: the Hyperliquid **API wallet** can't withdraw, and the meme bot's burner holds only working capital.
- Keys live only in each server's `.env`, never in code, chat, an LLM prompt or a log. LLMs only ever see summarised features, never wallet details.
- Withdrawal addresses are fixed in configuration, and changing them needs your action on the server.
- Each bot has its own server and its own wallet, so one compromise doesn't spread.

### 5.9 Revised build order

| # | Step | Takes | Needs from you | Why this order |
|---|---|---|---|---|
| **1** | **Hyperliquid funding recorder + carry backtest.** Record funding, spot and perp prices for every market, and replay the hedged carry with real fees | 2–3 days to build; uses public history | Tokyo VPS (1.7) | **Cheapest, quickest, no speed edge needed**, and it was measured live today. It also produces the HLP benchmark (rank 2) for free |
| 2 | Meme collector (Phase 1) **with the full filter list from 5.3**, including bundle and sniper detection | 2–3 days to build, then 7–14 days of collection | 1.1, 1.2, 1.3 | Runs in the background while everything else is built |
| 3 | Wallet vetting dataset for copy trading (5.2 rank 2) | 3–4 days | Dune or Birdeye key (Part 4) | Can be tested on history straight away, without waiting for forward data |
| 4 | Hyperliquid order-book recorder for market making (Video 3 plan) | 1 day, then 14 days of recording | 1.6 | Free data, gathered in parallel |
| 5 | **Go/no-go decisions** for carry (after step 1), meme momentum (after step 2), copy trading (after step 3) and market making (after step 4) | A few days each | — | Each one is judged against its benchmark: HLP, random entry, or random wallets |
| 6 | Paper trading for whatever passed, **with automatic profit sweeping turned on from day one** | 2 weeks | — | The sweep and kill switches get tested before real money |
| 7 | Tiny live test for whatever passed paper | 2–4 weeks | 1.5 and/or the API wallet + USDC | Real fills against paper |
| 8 | Allocation engine (5.7): money moves only to bots that keep beating their benchmark | Ongoing | Your approval for each scale-up | Capital follows measured edge, never hope |

**The honest bottom line, in the brief's words and mine:** a bot makes discipline and speed systematic; it doesn't create edge by itself. This plan is built to find out **cheaply** whether we have one, to put real money only behind what has proven itself out of sample, and to **take profits off the table daily** so that the losses most retail traders suffer can't wipe out what's already been made.
