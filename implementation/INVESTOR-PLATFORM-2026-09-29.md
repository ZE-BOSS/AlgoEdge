# AlgoEdge Capital — investor platform, admin console, mobile app

**2026-09-29** · plan, not yet built · supersedes nothing

---

## 0. Read this part first

There is one decision in this document that everything else depends on, and getting
it wrong is expensive to undo once real money is in: **how an investor's share of the
pool is recorded.**

You described it as "split the profit depending on the percentage per capital of each
user". That works only if everybody deposits on the same day and nobody ever adds or
withdraws. The moment a fourth investor joins in month three, percentage-of-capital
silently hands them a share of profit they were not there for.

**Worked example.** A and B each put in $1,000. The pool trades to $3,000 — $1,000 of
profit. C now deposits $1,000, so the pool is $4,000 and C owns 25% of the capital.
The next day the pool makes $400. Split by capital share, C takes $100 of it. Fine so
far. But if you ever recompute *total* profit by capital share — which is what "split
depending on percentage per capital" means literally — C is credited 25% of the
$1,400 lifetime profit, i.e. $350, of which $250 was earned before C's money arrived.
A and B are robbed of $125 each, and neither the code nor the dashboard would show
anything wrong.

**The fix is the industry standard: unitisation.** The fund issues *units*. An
investor's stake is a number of units, not a percentage. Net asset value per unit
(NAV/unit) moves with the pool's P&L.

```
NAV/unit  = (pool equity - undeployed cash owed out) / units in issue
deposit   -> units bought  = amount / NAV_at_deposit
withdrawal-> units redeemed = amount / NAV_at_withdrawal
an investor's value = their units x today's NAV/unit
```

Start NAV/unit at 1.0000 (or 100.0000 — arbitrary, pick one and never change it).
With this, a mid-period deposit is automatically and provably fair, performance fees
are computable per investor, and every statement reconciles to the penny. Investor
percentage becomes a *derived display number* (`their units / units in issue`) rather
than the stored truth.

Everything below assumes unitisation. **If you want percentage-of-capital instead, say
so and I will build it, but I would be doing it over this objection** — it is the single
most common cause of investor disputes in small funds, and it cannot be retrofitted
cleanly once there are deposits at different NAVs.

### Two things I have to flag once, plainly

**1. Pooling other people's money to trade is a regulated activity.** In Nigeria that
is SEC Nigeria's remit (fund/portfolio management registration); most other
jurisdictions are similar. This document is an engineering plan and I will build
exactly what you have asked for — but the platform will be more convincing to
investors, not less, if the legal wrapper exists, and you should get that advice before
you take the first deposit rather than after.

**2. "Admin can update the numbers the user sees" needs care in how it is built.**
There are entirely legitimate reasons for the investor-facing number to differ from the
raw MT5 number: a deposit that has cleared your bank but is not yet funded to the
broker, fees, rounding, a trade that was partly hedged. Those are *adjustments*, and
every serious fund has them.

What separates an adjustment from a misrepresentation is that an adjustment is
**recorded, attributed, reasoned and reversible**. So I will build it that way: every
override writes an `adjustment` row with who, when, old value, new value and a
mandatory reason; nothing is ever silently overwritten; and the admin reconciliation
screen shows raw vs adjusted vs published side by side. That is also simply better
operationally — it is how you answer "why is my number different?" in ten seconds
instead of an afternoon. I am not going to build a silent-overwrite path.

---

## 1. What was fixed today, for context

Your profit-target report was correct and it was a real bug — see
`implementation/PROFIT-TARGET-FIXES-2026-09-29.md`. Summary: positions that survived a
bot restart were invisible to the profit target, so a trade at +$97 against a $50
target was never closed and nothing was logged. Fixed, with the live scenario pinned as
a regression test. Also added the **CLOSE** action you asked for (bank it, don't pause)
and moved every day/week/month boundary to **West African Time**.

---

## 2. Shape of the system

Three front ends, one backend, one database.

```
                    ┌──────────────────────────────┐
  admin.algoedge…   │  Admin console (existing app)│  bot control, slots,
                    │  + new Investors section     │  backtests, approvals
                    └──────────────┬───────────────┘
                                   │
  user.algoedge…    ┌──────────────┴───────────────┐
                    │  Investor web app (new)      │  same API, investor role
                    └──────────────┬───────────────┘
   Android APK      ┌──────────────┴───────────────┐
                    │  Expo mobile app (new)       │  same API, same design
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────┴───────────────┐
                    │  FastAPI backend (existing)  │
                    │  + backend/investor/ module  │
                    └──────────────────────────────┘
```

**The trading engine is not touched.** The investor platform reads from it and never
writes to it. That separation is deliberate: a bug in the investor portal must not be
able to affect a live position, and an investor request must never reach MT5.

### Repo layout

```
backend/investor/          NEW — domain logic, isolated from trading
  models.py                investors, units, deposits, withdrawals, NAV, adjustments
  nav.py                   NAV/unit calculation and the daily snapshot
  units.py                 subscribe / redeem / transfer
  disclosure.py            which trades an investor may see
  reconcile.py             raw vs adjusted vs published
  fees.py                  management + performance fee accrual
backend/api/routes/
  investor_*.py            investor-facing routes (role: investor)
  admin_investor_*.py      admin-facing routes (role: admin)
backend/notify/
  resend_client.py         email transport
  templates/               MJML -> HTML
frontend/                  existing admin app + new Investors section
investor-web/              NEW — React/Vite, its own build, its own origin
mobile/                    NEW — Expo (React Native), Android first
packages/api-client/       shared TS client + types, used by web and mobile
```

---

## 3. Data model

New tables. Money is `NUMERIC(18,2)`, units are `NUMERIC(24,8)` — **never floats.**
Float arithmetic on money is how a fund ends up one cent out on every statement.

| Table | Holds | Notes |
|---|---|---|
| `investors` | identity, KYC status, bank details, payout account | separate from `users` (admins) |
| `investor_units` | running unit balance per investor | derived, rebuildable from the ledger |
| `unit_transactions` | **the ledger**: every subscribe/redeem, units, NAV used | append-only, never updated |
| `deposits` | amount, method, proof, state machine, admin who confirmed | |
| `withdrawals` | amount, destination account, cap check, approval chain | |
| `nav_snapshots` | daily NAV/unit, pool equity, units in issue | one row per accounting day (WAT) |
| `trade_disclosures` | which closed trade is published, to whom, with what edits | links to `trades` |
| `adjustments` | any admin override: entity, field, old, new, reason, actor | the audit spine |
| `profit_splits` | investor's share %, and per-trade overrides | per-trade beats the default |
| `fee_accruals` | management and performance fees | high-water mark per investor |
| `email_log` | every message sent, to whom, provider id, status | |
| `app_releases` | platform, version, APK/IPA url, notes, is_current | drives the download button |
| `audit_log` | every admin action, immutable | |

`unit_transactions` is the source of truth. `investor_units` is a cache that must be
reproducible by replaying the ledger — and a nightly job asserts exactly that. If they
ever disagree, the platform has a bug and you want to know that night, not at
withdrawal time.

---

## 4. Money flows

### Deposit

Two methods, as you described:

1. **Bank transfer.** Admin sets the receiving bank account in the admin console
   (`settings/banking`); the investor sees those details and a reference code unique to
   them. They mark "I have sent it" with an optional proof upload. Admin sees it in a
   queue and confirms the amount actually received — **the admin's figure is what is
   credited, not the investor's claim.**
2. **Pre-existing / off-platform.** Admin records a deposit on the investor's behalf,
   including historic ones, with an effective date. This is how you onboard the people
   who are already in.

```
requested -> claimed_sent -> confirmed -> units_issued
                          \-> rejected (reason required)
```

Units are issued at the NAV of the **confirmed** date, not the claimed date. That is
the honest choice — the money was not at risk until it arrived — and it is the one
you can defend.

### Withdrawal

- Investor requests an amount and a destination account.
- **Capped at 30% of that investor's profit in the current calendar month (WAT).** The
  cap is computed server-side and shown live in the form, with the number that produced
  it, so the investor sees *why*.
- Above the cap, the request becomes an **exception request** with a mandatory
  justification, and goes to a separate admin queue.
- Admin approves → units redeemed at that day's NAV → payout marked sent with a
  reference.

```
requested -> approved -> paid
          \-> exception_pending -> approved/declined
          \-> declined (reason required)
```

### Revocation (full exit)

Investor requests closure. Admin sees the full position: units, current value,
fees owed, net payable. Admin pays out, then approves — **and approval is what deletes
the account**, exactly as you specified. The `unit_transactions` and `audit_log` rows
survive deletion in anonymised form, because a fund must be able to reconstruct its own
history; personal data is purged. That is also what GDPR-style "right to erasure"
regimes actually require, so it is not a compromise.

---

## 5. What an investor sees, and the reconciliation screen

**Investor dashboard:** capital in, current value, profit (absolute and %), their
share of the pool, NAV/unit chart, deposits/withdrawals history, statements (monthly
PDF), and the trade list *they have been shown*.

**Trade disclosure.** Every closed trade lands in an admin queue as `undisclosed`.
The admin can publish it as-is, publish it edited, or never publish it. Published
trades carry symbol, direction, date, and result — **not** the strategy name, the
parameters, or the entry logic. Strategy identity is stripped at the disclosure
boundary in `disclosure.py`, not in the frontend, so it cannot leak through an API
response someone inspects.

**The reconciliation screen (admin only)**, which is the thing you asked for:

| | Pool (raw) | Investor-facing (published) | Difference |
|---|---|---|---|
| Equity | from MT5 | sum of investor values + unallocated | Δ |
| Profit this month | from trades | sum of published allocations | Δ |
| Deposits | confirmed | credited | Δ (pending) |

Every non-zero Δ is clickable and resolves to the rows causing it. A healthy platform
runs at Δ = pending-deposits only; anything else is a flag.

---

## 6. Admin console additions

Inside the existing AlgoEdge app, a new **Investors** section:

- Investor list: units, value, profit, share %, state, last activity
- Deposit queue / withdrawal queue / exception queue / closure queue (badge counts)
- Add deposit on behalf of an investor; add historic profit
- Trade disclosure queue
- Reconciliation screen
- Banking details, fee schedule, profit-split defaults and per-trade overrides
- Mobile release manager: upload APK, set the current version, which drives the
  website's download button
- Audit log viewer

---

## 7. Email — Resend

`backend/notify/resend_client.py`, templates in MJML, every send logged to `email_log`
with the provider id so a bounce is traceable.

| Trigger | To | Why |
|---|---|---|
| Admin login (new device/IP) | admin | the security ask |
| Password reset | both | signed single-use token, 30 min, one use |
| Deposit confirmed / rejected | investor | |
| Withdrawal approved / paid / declined | investor | |
| Monthly statement | investor | PDF attached |
| Daily P&L digest | admin | opt-in, sent at the WAT day roll |
| Closure approved | investor | final statement |

Resend needs a verified sending domain with SPF, DKIM and DMARC records — without
those, statements land in spam. That is part of the domain work in §10.

---

## 8. Mobile app — Expo

Android only for now. You asked whether iOS can be done without an Apple Developer
account: **no, not for real investors.** Apple requires every iOS binary to be signed
by a paid account ($99/yr) before it will run on someone else's phone. The sideloading
workarounds (AltStore, Sideloadly) need the user to plug into a computer and re-sign
every 7 days, which is not something you can ask an investor to do.

**The fallback that works today:** the investor web app is built as an installable
**PWA**, so an iPhone user opens `app.alphavantiqcapital.com` in Safari and taps Share
-> Add to Home Screen. It gets the icon, the splash and a full-screen shell, and it
uses the same code as the website — no extra build. It cannot do native push on iOS
below 16.4, which is the only real loss. That is a few hours of work and covers iOS
until the $99 is worth spending.

- **Expo + React Native**, TypeScript, sharing `packages/api-client` with the web app so
  types and endpoints cannot drift.
- Same design language as the investor web app — same tokens, same components where
  React Native allows.
- Built with **EAS Build** producing a signed universal APK; `eas build -p android
  --profile production`.
- Uploaded through the admin console into `app_releases`; the website's download button
  reads the current release, so shipping an update is an upload, not a deploy.
- **Because it is sideloaded, not Play Store:** Android will warn on install from an
  unknown source, so the download page needs a short "how to install" note. There is
  no auto-update, so the app must check `app_releases` on launch and prompt when a
  newer version exists. Sign every build with the **same keystore** — lose it and users
  must uninstall before they can update. I will document the keystore handover
  explicitly; it is the one irreversible artefact in the whole build.
- Push notifications via Expo push (deposit confirmed, withdrawal paid, statement
  ready).
- Biometric unlock on a stored session token.

---

## 9. Landing page

Public, marketing, no login. Sections: what the programme is; how it works (pooled,
algorithmic, risk-managed); **selected** performance evidence; the team; FAQ; apply;
app download.

On performance claims — the strongest thing you can show is also the honest thing:
verified, dated, with drawdown alongside return, and clearly labelled backtest vs live.
`implementation/STRATEGIES-SHIPPED-2026-09-25.md` has real numbers with real caveats.
Publishing return without drawdown is what unsophisticated funds do and sophisticated
investors discount it immediately. Every published figure carries its period, its
drawdown, and whether it is simulated.

Strategy secrecy is preserved throughout: mechanism described in general terms
("systematic, multi-strategy, volatility-aware, hard risk limits"), never parameters.

---

## 10. Domain, DNS, TLS — and why the subdomain would not save

`alphavantiqcapital.com`, registered at Hostinger, expires 2027-04-28, auto-renew on.

### The reason you could not create the admin subdomain

Two separate things were in the way.

**1. Your DNS is not at Hostinger.** The domain's nameservers are:

```
ns1.vercel-dns.com
ns2.vercel-dns.com
```

Hostinger is the **registrar** (who you bought it from); Vercel is the **DNS host**
(who actually answers queries for it). Records created in Hostinger's DNS panel are
ignored by the entire internet while those nameservers are set — which is exactly why
that Subdomains tab sat empty and would not stick.

**2. "Subdomains" is the wrong tool anyway.** That Hostinger feature creates
subdomains for *websites hosted on Hostinger*. To point a name at your own VPS you
create a plain **A record**. There is no "subdomain" object involved.

### Pick one of these

**Option A — keep DNS at Vercel** (recommended if anything is deployed there). Do
everything in the Vercel dashboard → the domain → DNS:

| Type | Name | Value |
|---|---|---|
| A | `admin` | `16.60.51.87` |
| A | `app` | `16.60.51.87` |
| A | `api` | `16.60.51.87` |
| A | `@` | landing page host |

**Option B — move DNS to Hostinger.** In Hostinger → DNS/Nameservers → change
nameservers to Hostinger's own, wait for propagation (up to 24h, usually much less),
then add the same A records under **DNS records** — *not* under Subdomains.

Do not split them. Whichever holds the nameservers holds all the records; a half-moved
zone is the most confusing failure mode in DNS.

### Then

1. Install Caddy on the VPS as a reverse proxy — it obtains and renews Let's Encrypt
   certificates automatically, which removes the "Not secure" warning you are seeing.
2. Proxy `admin.` and `app.` to their static builds, `api.` to uvicorn on :8000.
3. Lock CORS to those origins; close every other port at the firewall.
4. Add Resend's SPF, DKIM and DMARC TXT records **in whichever DNS host you chose** —
   without them, investor statements go to spam.
5. Put the admin console behind an IP allowlist or VPN **in addition** to login.

A minimal Caddyfile is about fifteen lines and I will write it when we get there.

## 11. Security baseline

Non-negotiables, because this holds money movement instructions:

- Separate roles with separate token audiences; an investor token is rejected by every
  admin route at the dependency level, not by a UI check.
- 2FA (TOTP) mandatory for admin.
- Argon2id password hashing; reset tokens single-use, 30 minutes.
- Rate limiting on auth, deposit and withdrawal endpoints.
- Every state change writes `audit_log` with actor, IP and before/after.
- Withdrawal destination changes trigger an email and a cooling-off period — this is
  the single most-abused flow in any investment platform.
- Nightly ledger-vs-cache integrity assertion, alerting on mismatch.
- Database backups off the VPS, restore tested, not just taken.

---

## 12. Phasing

Each phase ends with something usable, because a nine-week big-bang is how this fails.

| Phase | Delivers | Rough |
|---|---|---|
| **1. Ledger** | unitisation, NAV snapshots, investor/deposit/withdrawal tables, admin-entered deposits, reconciliation — **no investor login yet** | 1–1.5 wk **Done** — `PHASE-1-LEDGER-2026-09-29.md` |
| **2. Admin console** | Investors section, all queues, disclosure, adjustments, audit log | 1–1.5 wk **Done** — `PHASE-2-ADMIN-CONSOLE-2026-09-29.md` |
| **3. Investor web** | auth, dashboard, statements, deposit/withdrawal requests, trade list | 1.5–2 wk **Done** (PDF statements move to Phase 4) — `PHASE-3-INVESTOR-WEB-2026-09-29.md` |
| **4. Email** | Resend, all templates, statements as PDF | 0.5 wk |
| **5. Landing + domain** | public site, DNS, TLS, CORS, hardening | 1 wk |
| **6. Mobile** | Expo Android, EAS build, release manager, push | 1.5–2 wk |
| **7. Hardening** | 2FA, rate limits, backups, integrity job, pen-test pass | 1 wk |

Phase 1 first and alone. Once the ledger is right, everything else is presentation;
if the ledger is wrong, everything else is confidently-presented wrong numbers.

---

## 13. Decisions — LOCKED 2026-09-29

All seven answered. These are now the spec, not open questions.

| # | Decision | Consequence |
|---|---|---|
| 1 | **Unitisation: YES** | `unit_transactions` is the source of truth; percentage is display-only |
| 2 | **Performance fee AND management fee**, both admin-set | high-water mark per investor; accrued in `fee_accruals` |
| 3 | **Withdrawal cap = 30% of the investor's profit in the current month (WAT)**, and the **percentage is admin-configurable** | not 30% of capital; stored as a setting, default 30 |
| 4 | **Base currency USD** | NGN deposits, if any, are converted at a recorded rate stored on the deposit row |
| 5 | **Minimum $200**, **lock-up 1 month**, **notice period 1 week** — all three admin-updatable | stored as settings, not constants; changes apply to NEW commitments only |
| 6 | **Domain: `alphavantiqcapital.com`** (Hostinger registrar, Vercel DNS) | see §10 |
| 7 | **No Apple Developer account** | Android only. iOS is not possible without one — see §8 |

Two things that follow from #5 and need care at build time:

- **A lock-up and a notice period are promises to an investor**, so changing them must
  not retroactively trap money already committed under the old terms. The settings are
  versioned and each `unit_transaction` records the terms in force when it was made.
- **The 30% cap is monthly profit, not capital.** An investor whose account is flat for
  the month can withdraw nothing under the standard path and must use the exception
  request. That is a real customer-service edge and the UI must state the reason
  plainly rather than showing a disabled button.

### Brand

Company name is now **Alphavantiq Capital**, after the domain. Mark, favicon, lockup,
splash and loader are in `/brand` (see `brand/README.md`); the admin app is rebranded.

---

## 14. My honest read

The trading side is the hard part and it is largely done. This is a well-understood
build — the risk is not technical, it is in the accounting model and the money-movement
controls, which is why §0 and §11 are the long sections and the UI is not.

Two things I would add that you did not ask for, because they pay for themselves:

- **An investor-facing "how your number is calculated" page.** Most support load in
  small funds is "why is my balance X". A page that shows units × NAV with the inputs
  removes most of it.
- **A read-only "observer" role.** Lets you show the platform to a prospective investor
  without creating an account or exposing anyone's data.
