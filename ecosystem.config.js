/**
 * PM2 process definitions — `pm2 start ecosystem.config.js`
 *
 * The interpreter path is resolved at load time rather than hardcoded, because
 * the same file runs on the Windows workstation and on the Linux VPS fleet, and
 * the venv directory has never been named consistently between them:
 *
 *   Linux VPS   ->  venv/bin/python   |  .venv/bin/python
 *   Windows      ->  venv_win\Scripts\python.exe  |  .venv\Scripts\python.exe
 *
 * A hardcoded ".venv\Scripts\python.exe" silently failed on every host that
 * didn't happen to use that exact layout: PM2 reports the app as "errored" with
 * a bare spawn ENOENT and no indication that the path was the problem. Probing
 * the candidates and failing loudly with the list turns that into a one-line fix.
 */

const fs = require("fs");
const path = require("path");

const isWindows = process.platform === "win32";

// Ordered by preference. First existing path wins.
const PYTHON_CANDIDATES = isWindows
  ? [
      "venv_win/Scripts/python.exe",
      ".venv/Scripts/python.exe",
      "venv/Scripts/python.exe",
    ]
  : [
      "venv/bin/python",
      ".venv/bin/python",
      "venv_win/bin/python",
    ];

function resolvePython() {
  // Allow an explicit override for hosts with a non-standard layout:
  //   ALGOEDGE_PYTHON=/opt/algoedge/bin/python pm2 start ecosystem.config.js
  const override = process.env.ALGOEDGE_PYTHON;
  if (override) {
    if (!fs.existsSync(override)) {
      throw new Error(
        `[ecosystem] ALGOEDGE_PYTHON is set to "${override}" but that file does not exist.`
      );
    }
    return path.resolve(override);
  }

  for (const rel of PYTHON_CANDIDATES) {
    const abs = path.resolve(__dirname, rel);
    if (fs.existsSync(abs)) return abs;
  }

  throw new Error(
    `[ecosystem] No Python virtualenv found. Looked for:\n` +
      PYTHON_CANDIDATES.map((c) => `  - ${path.resolve(__dirname, c)}`).join("\n") +
      `\nCreate one, or set ALGOEDGE_PYTHON to the interpreter path.`
  );
}

const PYTHON = resolvePython();

// ── Behind Caddy (investor platform, Phase 5) ──────────────────────────────
// ALGOEDGE_CADDY=1 switches to the production layout in
// docs/DEPLOY-INVESTOR-PLATFORM.md:
//   * the backend listens on 127.0.0.1 only — reachable through Caddy, never
//     directly — and trusts Caddy's X-Forwarded-For, so rate limits and the
//     audit log see the visitor's address rather than 127.0.0.1 for everyone
//   * Caddy serves the sites on 80/443, so `vite preview` on :80 is not started
// Without it, nothing changes from the layout this file always had.
//
// These four settings may be set in the environment or in the repo's .env (the
// environment wins). Reading .env too means a setting there takes effect on the
// next `pm2 start`, without reopening PowerShell to see a new Machine variable.
const CADDY_KEYS = ["ALGOEDGE_CADDY", "CADDY_BIN", "ACME_EMAIL", "ADMIN_ALLOW_IPS"];

function readDotenv(keys) {
  const file = path.resolve(__dirname, ".env");
  const out = {};
  if (!fs.existsSync(file)) return out;
  for (const raw of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const m = raw.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/);
    if (!m || !keys.includes(m[1])) continue;
    out[m[1]] = m[2].replace(/^(["'])(.*)\1$/, "$2");
  }
  return out;
}

const DOTENV = readDotenv(CADDY_KEYS);
const setting = (key) => process.env[key] || DOTENV[key] || "";

const BEHIND_CADDY = setting("ALGOEDGE_CADDY") === "1";
const CADDY = setting("CADDY_BIN") || (isWindows ? "C:/caddy/caddy.exe" : "/usr/bin/caddy");
const BACKEND_ARGS = BEHIND_CADDY
  ? "-m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1"
  : "-m uvicorn backend.main:app --host 0.0.0.0 --port 8000";

module.exports = {
  apps: [
    {
      name: "algoedge-backend",
      script: PYTHON,
      args: BACKEND_ARGS,
      cwd: __dirname,
      interpreter: "none",
      autorestart: true,
      watch: false,
      // The backtester holds whole runs in memory; the default 1GB restart
      // threshold would kill a large run mid-flight.
      max_memory_restart: "2G",
      env: {
        NODE_ENV: "production",
      },
    },
    ...(BEHIND_CADDY ? [] : [{
      name: "algoedge-frontend",
      // `vite preview` serves frontend/dist — run `npm run build` first.
      script: path.resolve(__dirname, "frontend/node_modules/vite/bin/vite.js"),
      args: "preview --host 0.0.0.0 --port 80",
      cwd: path.resolve(__dirname, "frontend"),
      interpreter: "node",
      autorestart: true,
      watch: false,
      env: {
        NODE_ENV: "production",
      },
    }]),
    ...(BEHIND_CADDY ? [{
      name: "algoedge-caddy",
      script: CADDY,
      args: `run --config ${path.resolve(__dirname, "deploy/Caddyfile").replace(/\\/g, "/")} --adapter caddyfile`,
      cwd: __dirname,
      interpreter: "none",
      autorestart: true,
      watch: false,
      env: {
        // forward slashes, even on Windows: Caddy reads them fine and a
        // backslash in a Caddyfile placeholder is an escape
        ALGOEDGE_ROOT: __dirname.replace(/\\/g, "/"),
        ACME_EMAIL: setting("ACME_EMAIL"),
        ADMIN_ALLOW_IPS: setting("ADMIN_ALLOW_IPS") || "0.0.0.0/0 ::/0",
      },
    }] : []),
  ],
};
