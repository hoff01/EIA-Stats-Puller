# EIA Petroleum Stats (Legacy CSV)

Fast legacy-CSV-to-image generator for the EIA WPSR petroleum tables shown in the reference photos.

This version keeps the current stats output format but pulls from the legacy Weekly Petroleum Status Report page and table CSVs:

- Weekly page: `https://www.eia.gov/petroleum/supply/weekly/`
- Table 4 CSV: `https://ir.eia.gov/wpsr/table4.csv`
- Table 5A CSV: `https://ir.eia.gov/wpsr/table5a.csv`
- Table 6 CSV: `https://ir.eia.gov/wpsr/table6.csv`

It does not include natural gas storage logic.

## Setup

```bash
python3 -m pip install -r requirements.txt
```

`tzdata` is included in `requirements.txt` so the schedule runner can reliably interpret `America/New_York` on Windows.

## Run Once

```bash
python3 eia_stats.py --once
```

For testing without clipboard copy or image preview:

```bash
python3 eia_stats.py --once --latest --no-clipboard --no-preview --force
```

## Poll At Release

Poll every quarter second for one minute:

```bash
python3 eia_stats.py --poll --interval 0.5 --duration 120
```

The script writes `eia_stats.png`, archives a dated copy, and records generated release dates in `eia_stats_status.json` so the same release is not repeated.

Poll mode checks the Table 4 CSV header date before generating the image. The default interval is 0.5 seconds for up to 120 seconds, including retries of blank, unavailable and stale responses. The optional minimum is 0.25 seconds. Requests do not overlap; slow responses can extend the interval. After detection, the existing connection pool is reused for the remaining tables, with at most three concurrent downloads and retries only for failed downloads. Mixed release dates still stop publication. Clipboard copying and readback verification run immediately after rendering, before preview launch and archiving.

## Daily Schedule-Aware Runner

Use the included schedule runner when you want Windows Task Scheduler to launch the job once per day.

```bash
python3 wpsr_schedule_runner.py --show-decision
```

What it does:

- Refreshes the official EIA WPSR schedule from `https://www.eia.gov/petroleum/supply/weekly/schedule.php` immediately on first run, then again when the local cache is older than 7 days.
- Treats all schedule-page times as Eastern time.
- Fetches the latest published week on non-release days, independently of output history.
- On release days, waits until the official release time, then launches `eia_stats.py --poll`.

The current EIA page says the normal CSV/XLS release is after `10:30 a.m. New York/Eastern time` on Wednesday.

## Windows Task Scheduler

Use this as the task action after extracting the folder. The script resolves paths relative to its own folder, so the package can live anywhere. Schedule it once per day at any time before the release window on days you want it armed.

Program/script:

```powershell
powershell.exe
```

Add arguments:

```powershell
-NoProfile -ExecutionPolicy Bypass -File ".\run_eia_stats_task.ps1"
```

The PowerShell runner is self-contained: it moves into this folder, creates `.venv` if needed, installs `requirements.txt`, refreshes the official EIA schedule weekly, fetches the latest published data on non-release days, waits until the official Eastern release time on release days, polls for up to 120 seconds by default, writes `eia_stats.png`, updates `eia_stats_status.json`, archives the dated image, and logs to `logs\`.

Use `-Latest` (or `-IgnoreSchedule`) for an immediate latest-data fetch on any day, including before the scheduled release. This can return the previous published week before new data is released. Latest mode re-copies previously generated weeks; scheduled release mode retains duplicate protection. Direct Python use also supports `--poll --latest`, with the same 0.5-second/120-second retry limits. No existing image archive is required. Timeouts return a nonzero exit code.

By default, the generated PNG is opened on screen and copied to the active Windows clipboard so it can be pasted into a chat. Configure the task as "Run only when user is logged on" so Windows allows clipboard and preview access.

For a single inline Task Scheduler command instead of `-File`, use a relative folder from the task working directory:

```powershell
-NoProfile -ExecutionPolicy Bypass -Command "Set-Location $PWD; powershell.exe -NoProfile -ExecutionPolicy Bypass -File '.\run_eia_stats_task.ps1'"
```

To generate the image without opening it on screen, add `-NoPreview` to the PowerShell arguments. To generate without copying it to the clipboard, add `-NoClipboard`.

Useful switches:

```powershell
-ShowDecision
-RefreshScheduleOnly
-IgnoreSchedule
-DurationSeconds 60
-NoPreview
-NoClipboard
```

- `-ShowDecision` prints whether today is a release day and exits.
- `-RefreshScheduleOnly` updates the cached EIA schedule and exits.
- `-IgnoreSchedule` skips the schedule gate and starts polling immediately.
- `-DurationSeconds 60` is useful for quick testing instead of the default 120-second release window.
- `-NoPreview` skips opening `eia_stats.png` after generation.
- `-NoClipboard` skips copying `eia_stats.png` to the clipboard.

## Configuration

Environment variables supported by the recreated script:

```bash
export EIA_STATS_OUTPUT_PATH=eia_stats.png
export EIA_STATS_STATUS_FILE=eia_stats_status.json
export EIA_STATS_REFRESH_INTERVAL_SECONDS=0.5
export EIA_STATS_MAX_ATTEMPTS=240
export EIA_STATS_RUN_MODE=poll
export EIA_STATS_REQUEST_TIMEOUT_SECONDS=2.5
export EIA_STATS_IMAGE_FETCH_RETRY_ATTEMPTS=3
export EIA_STATS_IMAGE_FETCH_RETRY_SECONDS=0.25
export EIA_STATS_CLIPBOARD_RETRY_ATTEMPTS=5
export EIA_STATS_CLIPBOARD_RETRY_SECONDS=0.25
export EIA_STATS_TABLE4_URL=https://ir.eia.gov/wpsr/table4.csv
export EIA_STATS_TABLE5A_URL=https://ir.eia.gov/wpsr/table5a.csv
export EIA_STATS_TABLE6_URL=https://ir.eia.gov/wpsr/table6.csv
```

## Fast Path

The implementation avoids pandas, Matplotlib, Selenium, browser rendering, and zoom operations. It uses:

- HTTPX with HTTP/2 enabled for the live WPSR table fetches.
- A lightweight Table 4 probe for release detection.
- Parallel fetches for Tables 4, 5A, and 6 once a new release is detected.
- Direct CSV parsing with stable row labels from the legacy WPSR tables.
- A schedule-aware runner seeded from the official EIA holiday schedule and refreshed weekly.
- Pillow direct table drawing.
- Windows PowerShell clipboard copy or macOS `osascript` clipboard copy.
