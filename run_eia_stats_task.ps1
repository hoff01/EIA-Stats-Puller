param(
    [double]$IntervalSeconds = 0.5,
    [double]$DurationSeconds = 120,
    [double]$TimeoutSeconds = 2.5,
    [double]$ScheduleTimeoutSeconds = 20.0,
    [int]$ScheduleRefreshDays = 7,
    [switch]$Force,
    [switch]$NoClipboard,
    [switch]$NoPreview,
    [switch]$IgnoreSchedule,
    [switch]$RefreshScheduleOnly,
    [switch]$ShowDecision,
    [switch]$SetupOnly
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LiveRunner = Join-Path $ScriptDir "old_stats\run_eia_stats_task.ps1"

if (-not (Test-Path $LiveRunner)) {
    throw "Live EIA stats runner not found at $LiveRunner"
}

& $LiveRunner @PSBoundParameters
exit $LASTEXITCODE
