# OpsGraph one-command demo (Windows PowerShell).
# Starts UModel, loads the model + runtime graph, runs RCA, then serves the console.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$runtime = Join-Path $root ".runtime"
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

function Test-Url([string]$url) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri $url
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Wait-Url([string]$url, [int]$seconds = 40) {
    for ($i = 0; $i -lt $seconds; $i++) {
        if (Test-Url $url) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

$started = @()
$umodelUrl = "http://127.0.0.1:18080"
$consoleUrl = "http://127.0.0.1:18088"

if (-not (Test-Url "$umodelUrl/healthz")) {
    $binary = Join-Path $root "..\bin\umodel-server.exe"
    if (-not (Test-Path $binary)) {
        $repo = Resolve-Path (Join-Path $root "..\UnifiedModel")
        Write-Host "Building umodel-server from $repo ..."
        $env:GOTOOLCHAIN = "auto"
        Push-Location $repo
        try {
            go build -o $binary ./cmd/umodel-server
        } finally {
            Pop-Location
        }
    }
    if (-not (Test-Path $binary)) { throw "umodel-server.exe not found. Build UnifiedModel first." }

    $outLog = Join-Path $runtime "umodel.out.log"
    $errLog = Join-Path $runtime "umodel.err.log"
    $serverArgs = @(
        "--graphstore", "file.memory",
        "--data", (Join-Path $runtime "umodel"),
        "--addr", "127.0.0.1:18080",
        "--import-root", $root
    )
    $process = Start-Process -FilePath $binary -ArgumentList $serverArgs -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    $started += @{ name = "umodel"; pid = $process.Id }
    Write-Host "Started UModel (pid $($process.Id)) -> $umodelUrl"
    if (-not (Wait-Url "$umodelUrl/healthz" 40)) {
        throw "UModel did not become ready. See $errLog"
    }
}

Write-Host "Loading model and telemetry into UModel ..."
python -u -m opsgraph demo --addr $umodelUrl
if ($LASTEXITCODE -ne 0) { throw "OpsGraph pipeline failed" }

Write-Host "Running eBPF code-level RCA + REA loop ..."
python -u -m opsgraph code-demo
if ($LASTEXITCODE -ne 0) { throw "Code RCA/REA loop failed" }

Write-Host "Compressing alerts and attaching automated RCA ..."
python -u -m opsgraph alerts
if ($LASTEXITCODE -ne 0) { throw "Alert pipeline failed" }

Write-Host "Running 60%-coverage inference loop ..."
python -u -m opsgraph partial-demo
if ($LASTEXITCODE -ne 0) { throw "Partial-observability RCA failed" }

Write-Host "Writing on-call decision brief ..."
python -u -m opsgraph brief
if ($LASTEXITCODE -ne 0) { throw "Decision brief failed" }

if (-not (Test-Url "$consoleUrl/api/health")) {
    $outLog = Join-Path $runtime "console.out.log"
    $errLog = Join-Path $runtime "console.err.log"
    $process = Start-Process -FilePath "python" -ArgumentList @("-u", "-m", "opsgraph", "serve", "--port", "18088") `
        -WorkingDirectory $root -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    $started += @{ name = "console"; pid = $process.Id }
    Write-Host "Started console (pid $($process.Id)) -> $consoleUrl"
    if (-not (Wait-Url "$consoleUrl/api/health" 20)) {
        throw "Console did not become ready. See $errLog"
    }
}

if ($started.Count -gt 0) {
    $started | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $runtime "pids.json")
}

Write-Host ""
Write-Host "============================================================"
Write-Host "  OpsGraph console : $consoleUrl"
Write-Host "  UModel API       : $umodelUrl"
Write-Host "  Incident report  : $consoleUrl/api/report"
Write-Host "  Stop everything  : .\stop-demo.ps1"
Write-Host "============================================================"
Start-Process $consoleUrl
