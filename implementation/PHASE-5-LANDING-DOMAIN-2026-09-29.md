# Phase 5 — public website, domain, hardening

**2026-09-29** · `landing/`, `backend/api/routes/public.py`, `deploy/Caddyfile`,
`ecosystem.config.js`, `scripts/build-sites.ps1` · **`docs/DEPLOY-INVESTOR-PLATFORM.md` is
the step-by-step guide** · tests: `test_investor_public.py` (6)

## The website (`landing/`)

A static page with no framework and no build step. It covers:
- how it works
- the approach, in general terms only
- **live performance**
- what investors see
- terms
- FAQ
- the app download
- the application form
- a risk warning

It has been checked at 390px and 1366px wide: no sideways scroll and no page
errors. The form refuses to send without the risk acknowledgement, and it
submits correctly.

**Performance** is read live from the fund's unit price
(`GET /api/public/performance`): return since the start, the worst fall from a
peak, months tracked, a price chart, and monthly returns.

- **Return is never shown without the drawdown beside it.** Only live figures
  are shown, no backtests.
- **The figures are labelled as before fees.** Fees are taken by cancelling each
  investor's units, which leaves the unit price where it was. So the price
  series is the fund's return before fees, and an investor's own return is lower
  by their fees. A test pins this down, so the label can't drift from the
  maths.
- If there isn't enough history yet, the page says so rather than showing a
  single point.

**Terms** come from the live fund settings (`GET /api/public/terms`), so the
page can't disagree with what investors are held to.

**Applications** (`POST /api/public/apply`):
- consent required
- a hidden honeypot field for bots
- limited to 5 per hour per IP and per email
- they arrive in the admin's new **Applications** tab, which has a badge, and
  the admin is emailed

**Accept** creates a *pending* investor. No money moves and no login is sent;
that is the next step, from the investor's page.

**Company details** go in `landing/config.js`: the contact email, and a
`regulatory` line that stays hidden until filled. That line should be worded
exactly as your legal adviser gave it; I did not invent one.

**There is no team section** in the plan's list; I had no content for it.

## Domain and hardening

You chose Hostinger for DNS. The exact records, and the order to do things in,
are in `docs/DEPLOY-INVESTOR-PLATFORM.md`. It also covers the Elastic IP check,
Resend's DNS records with DMARC, the AWS security group, and Windows Firewall.

**Caddy** (`deploy/Caddyfile`) serves:
- the website
- `app.` and `admin.`, with deep links working
- `api.` as a reverse proxy to `127.0.0.1:8000`

It handles:
- automatic Let's Encrypt certificates
- HSTS, frame, content-type, referrer and permissions headers
- a content-security policy on the website and the investor app
- `www` redirecting to the main domain
- long caching for hashed assets and none for `index.html` and the service
  worker
- body size limits: 10 MB normally, 200 MB for APK uploads (Phase 6)

**The admin allow-list** (`ADMIN_ALLOW_IPS`) blocks the admin console **and
every operator API route** (trading, bot control, admin, auth) for anyone
outside the list, before FastAPI is even involved. Only `/api/investor/*`,
`/api/public/*` and `/api/health` stay open to the internet.

This was validated with Caddy 2.8.4 and **tested live locally**:
- operator routes and the admin site returned 403 from outside the list
- investor, public and health routes passed through
- deep links returned the app
- headers were present

**`ALGOEDGE_CADDY=1`** in `ecosystem.config.js` switches the VPS over:
- the backend listens on `127.0.0.1` only
- the backend trusts Caddy's forwarded address, so rate limits and the audit
  log see the visitor's IP. Tested: a forwarded address was recorded, and Caddy
  overwrites any `X-Forwarded-For` a visitor tries to send
- PM2 runs Caddy
- the old `vite preview` on port 80 is not started

Without the flag, nothing changes. Unsetting it is the rollback.

**`CORS_STRICT=1`** allows browser calls only from the four sites.

## Also fixed: rounding on cancelled units

Cancelling units (withdrawals and fees) rounded the unit count **down**. Every
payout therefore cancelled slightly fewer units than the cash it paid out. The
investor leaving kept a sliver, and everyone who stayed lost it. The amount is
about a millionth of a dollar each time, but it breaks the rule the code sets
itself for deposits: rounding never favours the person moving money over the
pool.

Cancellations now round **up** (`nav.units_to_cancel`). Full exits still land
exactly on zero units. Found by the fee/price test.

## Verified

- 119 investor tests pass.
- Admin console lint is unchanged at the 61 baseline, and the build is clean.
- Caddyfile validated, and its routing exercised live.
- The website was rendered at phone and desktop sizes with real data.

## Not verifiable from here

The things that only exist on your side:
- the Hostinger nameserver change
- certificate issuance, which needs the DNS pointing at the VPS
- the AWS and Windows firewall rules
- Resend domain verification

Each has a check in the guide's §6 table.
