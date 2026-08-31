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

module.exports = {
  apps: [
    {
      name: "algoedge-backend",
      script: PYTHON,
      args: "-m uvicorn backend.main:app --host 0.0.0.0 --port 8000",
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
    {
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
    },
  ],
};
