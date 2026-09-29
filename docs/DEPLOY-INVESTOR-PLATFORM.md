Alphavantiq Capital — putting the investor platform on the domain
=================================================================

What this sets up, on the existing Windows VPS (EC2, PM2):

| Address | Serves | From |
|---|---|---|
| `https://alphavantiqcapital.com` | the public website | `landing/` |
| `https://app.alphavantiqcapital.com` | the investor app (also the iPhone "app") | `investor-web/dist` |
| `https://admin.alphavantiqcapital.com` | the admin console | `frontend/dist` |
| `https://api.alphavantiqcapital.com` | the API | uvicorn on `127.0.0.1:8000` |

**Caddy** sits in front of all four. It handles the HTTPS certificates, which it
gets and renews by itself, and it is the only way in: the API stops listening on
the public network.

PowerShell 5.1 notes from `VPS-DEPLOYMENT.md` still apply: there is no `&&`, so
chain commands with `;`.

---

## 0. Before you start: check the server's address

The records below point at **`18.135.178.117`**. That address must never change.

1. Open **AWS Console → EC2 → Elastic IPs**.
2. Check that this address is an **Elastic IP** associated with the instance.

A plain EC2 public IP changes every time the instance is stopped and started,
and the whole domain would go dark. If it is not an Elastic IP, allocate one,
associate it, and use that address everywhere below.

---

## 1. DNS: move it to Hostinger

Right now the domain's nameservers are Vercel's. That is why records added in
Hostinger have no effect. You chose Hostinger, so move the nameservers there.

1. In **hPanel → Domains → alphavantiqcapital.com → DNS / Nameservers**, choose
   **Change nameservers → Use Hostinger nameservers**, and save.
2. In **Vercel**, remove `alphavantiqcapital.com` from any project that has it.
   A domain still attached there causes confusing half-states.
3. In **hPanel → DNS / Nameservers → DNS records**, delete any default parking
   `A` or `CNAME` records for `@` and `www`. Then add:

| Type | Name | Points to | TTL |
|---|---|---|---|
| A | `@` | `18.135.178.117` | 300 |
| A | `www` | `18.135.178.117` | 300 |
| A | `app` | `18.135.178.117` | 300 |
| A | `admin` | `18.135.178.117` | 300 |
| A | `api` | `18.135.178.117` | 300 |
| CAA | `@` | `0 issue "letsencrypt.org"` | 3600 |

These are plain **A records**. Do *not* use the "Subdomains" tab; that is for
websites hosted on Hostinger.

4. **Email (Resend).** In Resend → **Domains → Add domain → alphavantiqcapital.com**,
   Resend lists 3–4 records (an MX and TXT records for `send` and
   `resend._domainkey`). Add each one here exactly as shown. Then add a DMARC
   record:

| Type | Name | Value |
|---|---|---|
| TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:YOUR-ADDRESS@alphavantiqcapital.com` |

   Start DMARC at `p=none`. Once Resend shows the domain as **Verified** and mail
   is arriving, change it to `p=quarantine`.

**Propagation** usually takes minutes, but can take up to 24 hours. Check it from
your own computer:

```powershell
Resolve-DnsName app.alphavantiqcapital.com -Type A
Resolve-DnsName alphavantiqcapital.com -Type NS
```

When the NS records name Hostinger and the A records return your IP, continue.

---

## 2. Open 80/443, close everything else

**AWS → EC2 → Security Groups → the instance's group → Inbound rules**:

| Port | Source | Why |
|---|---|---|
| 80 | 0.0.0.0/0, ::/0 | Let's Encrypt checks + redirect to HTTPS |
| 443 | 0.0.0.0/0, ::/0 | the sites |
| 3389 | **your IP only** | Remote Desktop |

**Delete** any inbound rules for 8000, 5173 and 3000. The API must not be
reachable except through Caddy.

Then do the same on Windows. Open PowerShell as Administrator:

```powershell
New-NetFirewallRule -DisplayName "Caddy HTTP"  -Direction Inbound -Protocol TCP -LocalPort 80  -Action Allow
New-NetFirewallRule -DisplayName "Caddy HTTPS" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow
New-NetFirewallRule -DisplayName "Block API direct" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Block
```

---

## 3. Install Caddy

1. Download `caddy_*_windows_amd64.zip` from
   https://github.com/caddyserver/caddy/releases.
2. Put `caddy.exe` in `C:\caddy\`.
3. Check it:

```powershell
C:\caddy\caddy.exe version
```

---

## 4. Settings

**`.env`** (in the repo root; add these lines):

```ini
FRONTEND_URL=https://admin.alphavantiqcapital.com
ADMIN_APP_URL=https://admin.alphavantiqcapital.com
INVESTOR_APP_URL=https://app.alphavantiqcapital.com
PUBLIC_SITE_URL=https://alphavantiqcapital.com
CORS_STRICT=1
INVESTOR_JWT_SECRET=<a long random string, e.g. [guid]::NewGuid().ToString() twice>

RESEND_API_KEY=<your key>
EMAIL_MODE=live
EMAIL_FROM=Alphavantiq Capital <no-reply@alphavantiqcapital.com>
EMAIL_REPLY_TO=invest@alphavantiqcapital.com
ADMIN_ALERT_EMAIL=<where alerts go>
```

With `CORS_STRICT=1`, only these four sites may call the API from a browser.

**Machine environment for PM2 and Caddy.** Run these once, as Administrator:

```powershell
[Environment]::SetEnvironmentVariable("ALGOEDGE_CADDY", "1", "Machine")
[Environment]::SetEnvironmentVariable("CADDY_BIN", "C:/caddy/caddy.exe", "Machine")
[Environment]::SetEnvironmentVariable("ACME_EMAIL", "you@example.com", "Machine")
# Who may reach the admin console and every operator API route. Space-separated.
# Find your current address at https://ifconfig.me
[Environment]::SetEnvironmentVariable("ADMIN_ALLOW_IPS", "203.0.113.10", "Machine")
```

Close and reopen PowerShell so the new variables are picked up.

About `ADMIN_ALLOW_IPS`:
- If you leave it unset, **everyone** can reach the admin console (it still
  needs a login). Set it.
- If your home IP changes, update the variable and run `pm2 restart algoedge-caddy`.
- If you're locked out, Remote Desktop into the VPS and use
  `http://localhost:8000` from there.

---

## 5. Build and start

```powershell
cd C:\Users\Administrator\Documents\AlgoEdge
git pull origin dev
.\venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\build-sites.ps1
C:\caddy\caddy.exe validate --config deploy\Caddyfile --adapter caddyfile
pm2 delete all; pm2 start ecosystem.config.js; pm2 save
pm2 ls
```

`pm2 ls` should show **algoedge-backend** and **algoedge-caddy**, and *not*
algoedge-frontend: Caddy now serves the admin console.

The first request to each site makes Caddy fetch a certificate. It takes a few
seconds, once.

**The admin console remembers its old API address.** Anyone who used the console
before this change has the old `http://52.201.102.37:8000` address stored in
their browser. On the new console, go to **Settings → Connection** and set it to
`https://api.alphavantiqcapital.com`.

---

## 6. Check it

| Check | Expect |
|---|---|
| `https://alphavantiqcapital.com` | the website, padlock, live figures |
| `https://www.alphavantiqcapital.com` | redirects to the above |
| `https://app.alphavantiqcapital.com` | investor sign-in |
| `https://admin.alphavantiqcapital.com` from an allowed IP | admin console |
| the same from your phone on mobile data | **403 Not available from this network** |
| `https://api.alphavantiqcapital.com/api/health` | `{"status":"ok",…}` |
| `https://api.alphavantiqcapital.com/api/bot/status` from phone data | **403** |
| `http://18.135.178.117:8000` from outside | times out |
| Admin → Investors → an investor → **Email invitation** | Audit → Emails shows **sent** with a Resend id |
| https://www.ssllabs.com/ssltest/ on each host | A or A+ |

Every one of these was tested locally against the Caddyfile except the
certificates and the firewall, which only exist on the VPS: allow-listed and
blocked routes, SPA deep links, headers, and the forwarded client IP.

---

## Rolling back

```powershell
[Environment]::SetEnvironmentVariable("ALGOEDGE_CADDY", $null, "Machine")
```

Then reopen PowerShell and run:

```powershell
pm2 delete all; pm2 start ecosystem.config.js; pm2 save
```

That brings back the old layout (API on :8000, admin on :80). Open 8000 in the
security group again if you need it from outside.

## Updating later

```powershell
git pull origin dev
powershell -ExecutionPolicy Bypass -File scripts\build-sites.ps1
pm2 restart algoedge-backend
```

Caddy picks up new builds without a restart. Restart it only after editing
`deploy/Caddyfile` (`pm2 restart algoedge-caddy`).
