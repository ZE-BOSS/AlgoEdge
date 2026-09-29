# Build the three web front ends for production, pointed at the real API.
# Run from the repo root on the VPS:   powershell -ExecutionPolicy Bypass -File scripts\build-sites.ps1
#
# Each build bakes in the API address, so it must be rebuilt after changing it.
# The landing page needs no build (landing/config.js picks the API by hostname).

$ErrorActionPreference = "Stop"
$api = if ($env:API_URL) { $env:API_URL } else { "https://api.alphavantiqcapital.com" }
Write-Host "Building against $api"

Push-Location frontend
$env:VITE_DEFAULT_BACKEND_URL = $api
npm ci; if ($LASTEXITCODE -ne 0) { throw "frontend: npm ci failed" }
npm run build; if ($LASTEXITCODE -ne 0) { throw "frontend: build failed" }
Pop-Location

Push-Location investor-web
$env:VITE_API_URL = $api
npm ci; if ($LASTEXITCODE -ne 0) { throw "investor-web: npm ci failed" }
npm run build; if ($LASTEXITCODE -ne 0) { throw "investor-web: build failed" }
Pop-Location

Write-Host "Built frontend/dist and investor-web/dist. Caddy serves them as-is; no restart needed."
