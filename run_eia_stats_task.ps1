$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LiveRunner = Join-Path $ScriptDir "old_stats\run_eia_stats_task.ps1"

if (-not (Test-Path $LiveRunner)) {
    throw "Live EIA stats runner not found at $LiveRunner"
}

& $LiveRunner @args
exit $LASTEXITCODE
