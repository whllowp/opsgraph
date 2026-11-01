# Stop the processes started by run-demo.ps1.
$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $root ".runtime\pids.json"
if (Test-Path $pidFile) {
    $items = Get-Content $pidFile -Raw | ConvertFrom-Json
    foreach ($item in @($items)) {
        if ($item.pid) {
            Stop-Process -Id $item.pid -Force -ErrorAction SilentlyContinue
            Write-Host "Stopped $($item.name) (pid $($item.pid))"
        }
    }
    Remove-Item $pidFile -Force
} else {
    Write-Host "No .runtime\pids.json; nothing to stop."
}
