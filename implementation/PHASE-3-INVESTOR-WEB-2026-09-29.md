# Phase 3 — the investor web app

**2026-09-29** · `investor-web/` (new app) · `backend/investor/auth.py` ·
`backend/api/routes/investor_portal.py` · `tests/test_investor_portal.py` (16 tests)

Phase 3 of `INVESTOR-PLATFORM-2026-09-29.md`. Investors can now sign in, see
their position, add money, ask for withdrawals, see published trades, and ask to
close their account.

---

## Running it

```
cd investor-web && npm install
VITE_API_URL=http://localhost:8000 npm run dev      # http://localhost:5174
```

On the backend, set `INVESTOR_APP_URL` to the investor app's public address
(e.g. `https://app.alphavantiqcapital.com`). It is used two ways:

- It builds the invitation links, which point at `<INVESTOR_APP_URL>/accept?token=…`.
- It is added to CORS. `localhost:5174` is allowed by default for development.

For production, build with `VITE_API_URL=https://api.alphavantiqcapital.com npm run build`
and serve `investor-web/dist` from its own origin. It is a separate build on
purpose: no admin code ever ships to investors.

`INVESTOR_JWT_SECRET` is optional. When unset it is derived from
`JWT_SECRET_KEY`, and it is never equal to it. Set it explicitly in production
so the two keys can be rotated separately.

## Getting an investor in

1. Admin console → Investors → the investor → **Access & closure** → **Invitation link**.
2. Copy the link and send it to them. It works once, expires in 72 hours, and
   making a new link cancels the old one. Only its hash is stored, so it is shown
   exactly once. If it is lost, make another.
3. They open the link, choose a password (10–72 characters) and they are in.

A forgotten password uses the same panel: **Password reset link**, valid for 30
minutes. Phase 4 will email both kinds of link automatically.

---

## What the investor sees

| Tab | |
|---|---|
| Overview | Holding value, profit since joining, paid in / paid out, units, price per unit, share of the fund, a price-per-unit chart, and what is available to withdraw with the reason |
| Money → Add money | Our bank details, their personal reference code (with copy buttons), and "I have sent it" |
| Money → Withdraw | Holding and standard limit with the reason, updated live as they type; asks for a reason only when the amount needs review; names the account the money will go to |
| Activity | Withdrawals with their expected-by date, money in (including "you told us $X, $Y arrived"), and every movement of their units |
| Trades | Published trades only, as fund-level results |
| Account | Their details, the payout account (last four digits only), change password, close account |
| How this is calculated | Units × price = holding; holding + paid out − paid in = profit; their terms. This is the page from plan §14 |

It installs as an app. On iPhone that is Share → Add to Home Screen; on Android it
is the browser's install prompt. It has a manifest, icons rendered from the brand
mark (including a maskable one for Android), and a service worker. The service
worker caches the app **shell only, never API responses**: a cached balance
would be a stale number shown as if it were current.

---

## Security, and why it is built this way

**An investor token cannot reach an admin route, and an admin token cannot reach
an investor route. This relies on the signature, not on a role check.**
Investor tokens are signed with a different key and carry an audience claim.
The admin dependency checks signatures against the admin key, so an investor
token fails before any claim is read, and no admin route has to remember to
reject it. This is tested with the real admin auth dependency in the same app.

**No investor route takes an investor id.** The investor is taken from the
token, so there is no parameter anyone could change to read another account.

**Money only goes to the account on file.** A withdrawal request cannot name a
destination. That field is ignored, and there is a test that sends one. The
account on file can only be changed by the fund, with a recorded reason.
Without this, a stolen session would be enough to send the money anywhere.

**Changing a password signs out every other session**, refresh tokens
included. Each token carries a fingerprint of the password hash, so the reset
someone does after a scare actually locks the intruder out.

**Invite links are single-use and short-lived, and only their hash is
stored.** A leaked database row cannot be replayed as a login.

**Guessing is slowed down.** Five wrong passwords for one address in 15 minutes
lock that address for the rest of the window. An unknown address gets the same
answer and takes the same time as a wrong password, so the login page does not
reveal who is a client. The limit is in-memory, per process. Phase 7 moves it to
Redis along with the other rate limits.

**Passwords.** Hashed with bcrypt, as on the admin side. The plan named
Argon2id, but it isn't a dependency yet; switching is a one-line change and
passlib re-hashes each password at its next login. Passwords over 72 characters
are refused rather than silently cut short, which is what bcrypt would
otherwise do.

**What an investor never sees:** the fund's total equity, other investors,
strategy names, operator notes on ledger rows, or who approved what. They do
see the price per unit, because that is the price of their own units.

---

## One rule that was agreed but not enforced until now

**The 1-month lock-up (decision #5).** Phase 1 stored it but nothing checked
it. It now works like this:

- The month is counted from the investor's first deposit, under the terms that
  deposit was made under. A later, longer lock-up does not extend money already
  committed; that is tested.
- A top-up does not restart the clock. Restarting it would quietly turn a
  one-month promise into an indefinite one for anyone who adds money.
- Inside the lock-up a withdrawal is not refused. Like a request over the cap,
  it becomes an exception that needs a reason. The withdraw screen and the
  overview both say when the lock-up ends.

The notice period (1 week) is shown as a promise: "paid within 7 days", plus an
"expected by" date in Activity. Nothing blocks on it. It is a commitment the
fund makes about timing, not a rule the investor has to satisfy.

## Also fixed

Fee and limit percentages showed as "2.0000%" in the investor terms. This is the
same `NUMERIC(9,4)` formatting issue as the Phase 2 limit text. There is a test
for it.

---

## Verified

- **91 investor tests pass.** 16 of them are new portal tests over HTTP. The
  lock-up and session-ending tests were checked to fail with their guard removed.
  That check caught a lock-up test that was passing for the wrong reason (the cap
  alone forced a reason), and a test that isolates the lock-up was added.
- `investor-web`: lint clean, build clean. Admin console lint unchanged at the 61
  baseline.
- A Chromium walkthrough at 390px against a seeded database:
  - accepted an invitation (mismatched passwords were caught)
  - claimed a $1,500 deposit, which showed as "Waiting for us to confirm"
  - a $50 withdrawal went through as standard; for $900 the form asked for a
    reason, refused to send without one, then sent it for review
  - checked Activity, Trades, "How this is calculated" and Account
  - changed the password, then signed in again with the new one
  - no sideways scroll on any page, no console errors, no failed requests
- Desktop layout checked at 1280px. The admin's login-link panel was checked in
  the console.

## Not in Phase 3

- **Email** (Phase 4). Links are copied by hand for now.
- **PDF statements** (Phase 4).
- **Proof-of-payment upload** on deposit claims. The column exists, but upload
  storage does not.
- **Push notifications.** Those come with the Android app in Phase 6.
- **2FA for investors.** The plan makes 2FA mandatory for admins (Phase 7).
  Whether investors get it too is a decision for you.

## Next

Phase 4: Resend email. That means sending the invite and reset links (replacing
copy-and-paste), deposit and withdrawal notifications, and monthly PDF
statements.
