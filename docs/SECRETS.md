# Secrets: local `.env`, encrypted mirror for the VPS

The real keys live in `.env` at the repo root. `.env` is gitignored and must stay
that way — a key pushed to GitHub is a leaked key, private repo or not, because it
sits in the history forever and in every clone.

To move keys to the VPS we commit an **encrypted** copy instead:
`secrets/algoedge.env.enc`. Without the passphrase it is meaningless.

## First time (on this machine)

```bash
./scripts/secrets.sh encrypt
```

It prompts for a passphrase twice. Choose one you can retype on the VPS, store it
in your password manager, and never put it in the repo, in a commit message, or in
a chat window.

Then commit the encrypted file (you commit and push yourself):

```bash
git add secrets/algoedge.env.enc .env.example scripts/secrets.sh docs/SECRETS.md
git commit -m "Encrypted env mirror for VPS deploys"
git push origin dev
```

## On the VPS

```bash
git pull
./scripts/secrets.sh decrypt      # asks for the passphrase, writes .env (chmod 600)
pm2 restart algoedge-backend
```

## When a key changes

1. Edit `.env`.
2. `./scripts/secrets.sh encrypt`
3. Commit the new `secrets/algoedge.env.enc` and push.
4. On the VPS: `git pull && ./scripts/secrets.sh decrypt && pm2 restart algoedge-backend`

`./scripts/secrets.sh check` says whether the encrypted file still matches `.env`.

## Rules

- **Never** commit `.env`, `.env.bak.*`, or any file with a key in plain text.
- `.env.example` documents the variable names only, no values.
- Rotate a key if it was ever pasted somewhere it could be read (chat, screenshot,
  issue tracker, CI log). Regenerating a free key costs nothing.
- The keys currently stored are free tiers: FRED, Finnhub, Alpha Vantage, Tiingo,
  Polygon, Helius, Dune, Birdeye, CoinGecko, GoPlus. The GoPlus entry has an app
  key **and** a secret; treat the secret like a password.
- The MT5 credentials in `.env` are the sensitive ones — they control a real trading
  account. Anyone who obtains them can place trades.

## Alternative if you'd rather not commit secrets at all

Keep `.env` off git entirely and copy it to the VPS over SSH:

```bash
scp .env user@your-vps:/path/to/AlgoEdge/.env
```

That is the safest option; the encrypted-mirror flow exists because you asked to be
able to pull everything from GitHub.
