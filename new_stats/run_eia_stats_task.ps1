param(
    [double]$IntervalSeconds = 1.0,
    [double]$DurationSeconds = 120,
    [double]$TimeoutSeconds = 2.5,
    [double]$ScheduleTimeoutSeconds = 20.0,
    [int]$ScheduleRefreshDays = 7,
    [switch]$Force,
    [switch]$NoClipboard,
    [switch]$NoPreview,
    [switch]$IgnoreSchedule,
    [switch]$RefreshScheduleOnly,
    [switch]$ShowDecision
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonScript = Join-Path $ScriptDir "eia_stats.py"
$ScheduleRunner = Join-Path $ScriptDir "wpsr_schedule_runner.py"
$VenvDir = Join-Path $ScriptDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$Requirements = Join-Path $ScriptDir "requirements.txt"
$RequirementsStamp = Join-Path $VenvDir ".requirements.stamp"
$LogDir = Join-Path $ScriptDir "logs"
$LogFile = Join-Path $LogDir ("eia_stats_task_{0}.log" -f (Get-Date -Format "yyyyMMdd"))

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-TaskLog {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogFile -Value $line
}

function Invoke-LoggedCommand {
    param(
        [string]$Command,
        [string[]]$Arguments
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $Command @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $output | ForEach-Object { Add-Content -Path $LogFile -Value ([string]$_) }
    return $exitCode
}

function Get-SystemPython {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        return @("py", "-3")
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @($python.Source)
    }

    $python3 = Get-Command python3 -ErrorAction SilentlyContinue
    if ($python3) {
        return @($python3.Source)
    }

    throw "Python 3 was not found on PATH."
}

Set-Location $ScriptDir
$env:EIA_STATS_OUTPUT_PATH = Join-Path $ScriptDir "eia_stats.png"
$env:EIA_STATS_STATUS_FILE = Join-Path $ScriptDir "eia_stats_status.json"
$env:EIA_STATS_REFRESH_INTERVAL_SECONDS = [string]$IntervalSeconds
$env:EIA_STATS_MAX_ATTEMPTS = [string][int][Math]::Ceiling($DurationSeconds / $IntervalSeconds)
$env:EIA_STATS_RUN_MODE = "poll"
$env:EIA_STATS_REQUEST_TIMEOUT_SECONDS = [string]$TimeoutSeconds
$env:EIA_STATS_IMAGE_FETCH_RETRY_ATTEMPTS = "3"
$env:EIA_STATS_IMAGE_FETCH_RETRY_SECONDS = "0.25"
$env:EIA_STATS_CLIPBOARD_RETRY_ATTEMPTS = "5"
$env:EIA_STATS_CLIPBOARD_RETRY_SECONDS = "0.25"

if (-not (Test-Path $VenvPython)) {
    Write-TaskLog "Creating local Python virtual environment."
    $systemPython = @(Get-SystemPython)
    $pythonCommand = $systemPython[0]
    $pythonArgs = @()
    if ($systemPython.Count -gt 1) {
        $pythonArgs = $systemPython[1..($systemPython.Count - 1)]
    }
    $venvExitCode = Invoke-LoggedCommand -Command $pythonCommand -Arguments ($pythonArgs + @("-m", "venv", $VenvDir))
    if ($venvExitCode -ne 0) {
        throw "Failed to create the virtual environment with exit code $venvExitCode."
    }
}

$installRequirements = -not (Test-Path $RequirementsStamp)
if (-not $installRequirements) {
    $installRequirements = (Get-Item $Requirements).LastWriteTimeUtc -gt (Get-Item $RequirementsStamp).LastWriteTimeUtc
}

if ($installRequirements) {
    Write-TaskLog "Installing/updating required Python packages."
    $pipExitCode = Invoke-LoggedCommand -Command $VenvPython -Arguments @("-m", "pip", "install", "--disable-pip-version-check", "-r", $Requirements)
    if ($pipExitCode -ne 0) {
        throw "Failed to install Python packages with exit code $pipExitCode."
    }
    New-Item -ItemType File -Force -Path $RequirementsStamp | Out-Null
}

$argsList = @(
    $ScheduleRunner,
    "--stats-script", $PythonScript,
    "--interval", [string]$IntervalSeconds,
    "--duration", [string]$DurationSeconds,
    "--timeout", [string]$TimeoutSeconds,
    "--schedule-timeout", [string]$ScheduleTimeoutSeconds,
    "--schedule-refresh-days", [string]$ScheduleRefreshDays,
    "--output", $env:EIA_STATS_OUTPUT_PATH,
    "--status-file", $env:EIA_STATS_STATUS_FILE
)

if ($NoClipboard) {
    $argsList += "--no-clipboard"
}
if ($NoPreview) {
    $argsList += "--no-preview"
}
if ($Force) {
    $argsList += "--force"
}
if ($IgnoreSchedule) {
    $argsList += "--ignore-schedule"
}
if ($RefreshScheduleOnly) {
    $argsList += "--refresh-only"
}
if ($ShowDecision) {
    $argsList += "--show-decision"
}

Write-TaskLog "Running EIA schedule-aware task."
$exitCode = Invoke-LoggedCommand -Command $VenvPython -Arguments $argsList
Write-TaskLog "Finished with exit code $exitCode."

if ($exitCode -eq 2) {
    exit 0
}
exit $exitCode
