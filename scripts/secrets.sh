#!/usr/bin/env bash
# Keys in git, safely.
#
#   ./scripts/secrets.sh encrypt     .env  ->  secrets/algoedge.env.enc   (commit this)
#   ./scripts/secrets.sh decrypt     secrets/algoedge.env.enc  ->  .env   (on the VPS)
#
# AES-256-CBC with PBKDF2. The passphrase is typed at the prompt, never stored and
# never committed — without it the encrypted file is useless to anyone who clones
# the repo. Plain .env stays gitignored; only the .enc file is committed.
#
# On the VPS:  git pull && ./scripts/secrets.sh decrypt && pm2 restart algoedge-backend
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENC="$ROOT/secrets/algoedge.env.enc"
PLAIN="$ROOT/.env"
ITER=200000

command -v openssl >/dev/null || { echo "openssl not found (Git Bash ships it on Windows)"; exit 1; }

case "${1:-}" in
  encrypt)
    [ -f "$PLAIN" ] || { echo "no $PLAIN to encrypt"; exit 1; }
    mkdir -p "$ROOT/secrets"
    openssl enc -aes-256-cbc -pbkdf2 -iter "$ITER" -salt -in "$PLAIN" -out "$ENC"
    echo "wrote $ENC  ($(wc -c < "$ENC") bytes) — safe to commit"
    ;;
  decrypt)
    [ -f "$ENC" ] || { echo "no $ENC found"; exit 1; }
    if [ -f "$PLAIN" ]; then
      cp "$PLAIN" "$PLAIN.bak.$(date +%Y%m%d%H%M%S)"
      echo "existing .env backed up"
    fi
    openssl enc -d -aes-256-cbc -pbkdf2 -iter "$ITER" -in "$ENC" -out "$PLAIN"
    chmod 600 "$PLAIN" 2>/dev/null || true
    echo "wrote $PLAIN"
    ;;
  check)
    # Does the encrypted file still match the current .env? (no secrets printed)
    tmp="$(mktemp)"
    openssl enc -d -aes-256-cbc -pbkdf2 -iter "$ITER" -in "$ENC" -out "$tmp"
    if diff -q "$tmp" "$PLAIN" >/dev/null; then echo "in sync"; else echo "DIFFERENT — re-run encrypt"; fi
    rm -f "$tmp"
    ;;
  *)
    echo "usage: $0 {encrypt|decrypt|check}"
    exit 1
    ;;
esac
