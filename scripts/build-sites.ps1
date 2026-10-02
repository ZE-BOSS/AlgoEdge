# Build the web front ends for production, pointed at the real API.
# Run from the repo root on the VPS:   powershell -ExecutionPolicy Bypass -File scripts\build-sites.ps1
#
# Each build bakes in the API address, so it must be rebuilt after changing it.
# The landing page needs no build (landing/config.js picks the API by hostname).

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# WHERE THE API ADDRESS COMES FROM, AND WHY IT IS READ FROM .env
#
# The address is BAKED INTO THE BUILD. Defaulting it to the live API meant a
# build on any other machine silently produced a front end that talked to
# PRODUCTION -- the exact accident a demo server exists to avoid, and one that
# leaves no trace until someone notices demo showing live trades.
#
# So it is read from the repo's .env, which is gitignored and therefore already
# per-machine, the same way ecosystem.config.js reads ALGOEDGE_CADDY. The
# environment still wins, so a one-off override is still possible.
#
#   live .env:  API_URL=https://api.alphavantiqcapital.com
#   demo .env:  API_URL=http://<demo-ip>:8000
#
# With that set on each box, the SAME deploy command is correct on both.
function Get-DotenvValue($key) {
    $file = Join-Path $root ".env"
    if (-not (Test-Path $file)) { return $null }
    foreach ($line in Get-Content $file) {
        if ($line -match "^\s*(?:export\s+)?$key\s*=\s*(.*?)\s*$") {
            return $matches[1].Trim("'", '"')
        }
    }
    return $null
}

$api = $env:API_URL
if (-not $api) { $api = Get-DotenvValue "API_URL" }
if (-not $api) {
    throw "API_URL is not set. Add it to .env on this machine -- " +
          "live: https://api.alphavantiqcapital.com, demo: http://<demo-ip>:8000. " +
          "Refusing to guess, because guessing the live API on a demo box is the " +
          "one mistake worth failing loudly over."
}
Write-Host "Building against $api"

# Windows will not delete a file a running program has loaded. The old PM2 app
# "algoedge-frontend" runs `vite preview` out of frontend\node_modules, which
# keeps Vite's native binary open — and `npm ci` must delete node_modules first,
# so it fails with EPERM on rolldown-binding.win32-x64-msvc.node. Stop it first.
# (Behind Caddy it is not used at all; ALGOEDGE_CADDY=1 stops PM2 starting it.)
# Run through cmd so pm2's "not found" (normal behind Caddy, where that app no
# longer exists) is not turned into a terminating error by PowerShell 5.1,
# which treats any native stderr as an error under $ErrorActionPreference = Stop.
if (Get-Command pm2 -ErrorAction SilentlyContinue) {
    cmd /c "pm2 stop algoedge-frontend >nul 2>&1"
    Start-Sleep -Seconds 2
}

function Build-App($dir, $envName) {
    Push-Location (Join-Path $root $dir)
    try {
        Set-Item -Path "env:$envName" -Value $api
        npm ci
        if ($LASTEXITCODE -ne 0) {
            throw "$dir : npm ci failed. If it says EPERM, something still has a file in $dir\node_modules open: " +
                  "run 'pm2 ls' and stop anything serving from $dir, close any editor or terminal running " +
                  "'npm run dev' there, then run this script again."
        }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "$dir : build failed" }
    } finally {
        Pop-Location          # back where you started, even when it fails
    }
}

Build-App "frontend" "VITE_DEFAULT_BACKEND_URL"
Build-App "investor-web" "VITE_API_URL"

Write-Host "Built frontend\dist and investor-web\dist. Caddy serves them as-is; no restart needed."
