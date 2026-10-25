# deploy/linux/pack.ps1 - zip what a Linux backend needs, from this Windows PC.
#
#   powershell -ExecutionPolicy Bypass -File deploy\linux\pack.ps1
#   scp algoedge-src.zip root@<VPS-IP>:/opt/algoedge/
#
# Code + the model/agent files the backend loads. Leaves out the venvs, the
# 1 GB of research data, logs, databases and every .env (secrets go in by hand).
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$out = Join-Path $root "algoedge-src.zip"
$stage = Join-Path $env:TEMP "algoedge-src"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory $stage | Out-Null

$items = @("backend", "deploy", "scripts", "models", "requirements.txt", "requirements-kronos.txt", "pytest.ini")
foreach ($i in $items) {
    $src = Join-Path $root $i
    if (Test-Path $src) { Copy-Item $src (Join-Path $stage $i) -Recurse }
}
# The data the RUNNING backend reads (agents, Kronos forecasts), not the research sets.
foreach ($d in @("data\rl", "data\kronos", "data\llm_budget.json")) {
    $src = Join-Path $root $d
    if (Test-Path $src) {
        $dst = Join-Path $stage $d
        New-Item -ItemType Directory (Split-Path $dst) -Force | Out-Null
        Copy-Item $src $dst -Recurse
    }
}
Get-ChildItem $stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem $stage -Recurse -File -Include ".env", "*.db", "*.log" | Remove-Item -Force

if (Test-Path $out) { Remove-Item $out -Force }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $out
Remove-Item $stage -Recurse -Force
"{0}  ({1:N0} MB)" -f $out, ((Get-Item $out).Length / 1MB)
