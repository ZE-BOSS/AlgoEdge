# Build the web front ends for production, pointed at the real API.
# Run from the repo root on the VPS:   powershell -ExecutionPolicy Bypass -File scripts\build-sites.ps1
#
# Each build bakes in the API address, so it must be rebuilt after changing it.
# The landing page needs no build (landing/config.js picks the API by hostname).

$ErrorActionPreference = "Stop"
$api = if ($env:API_URL) { $env:API_URL } else { "https://api.alphavantiqcapital.com" }
$root = Split-Path -Parent $PSScriptRoot
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
