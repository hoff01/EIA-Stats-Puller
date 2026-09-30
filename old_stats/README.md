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

## Immediate Live Polling

Fetch immediately, with up to 120 total attempts and 0.4 seconds between attempts:

```bash
python3 eia_stats.py --poll --latest --interval 0.4 --max-attempts 120
```

The script writes `eia_stats.png`, copies it to the clipboard, opens the preview, archives a dated copy and records the result in `eia_stats_status.json`. Latest mode republishes the current live week even if it was generated before; output history does not choose the reporting week.

Poll mode checks the Table 4 CSV header date before generating the image. Blank, unavailable, HTML/error or inconsistent responses are retried until valid data is ready or the attempt limit is reached. The default interval is 0.4 seconds and the default limit is 120 total attempts; the optional minimum interval is 0.25 seconds. Requests do not overlap. All 120 failed attempts involve 119 pauses (47.6 seconds) plus network and processing time, not a two-minute timer. `--duration` adds an optional time limit (default 0 means no separate time limit). After detection, the existing connection pool is reused for the remaining tables, with at most three concurrent downloads and retries only for failed downloads. Mixed release dates still stop publication. Clipboard copying and readback verification run immediately after rendering, before preview launch and archiving.

## Windows Runner

Use the included runner directly or from Windows Task Scheduler. Despite its compatibility filename, it starts fetching immediately by default, without a weekday or release-time gate.

```bash
python3 wpsr_schedule_runner.py --show-decision
```

The default run fetches the latest week EIA currently publishes, including on non-release days and before release time. Before new data is published, this may be the previous week. It does not need an existing image archive.

Only `--scheduled` (PowerShell `-Scheduled`) opts into the older calendar behavior:

- Refreshes the official EIA WPSR schedule from `https://www.eia.gov/petroleum/supply/weekly/schedule.php` immediately on first run, then again when the local cache is older than 7 days.
- Treats all schedule-page times as Eastern time.
- Fetches the latest published week on non-release days, independently of output history.
- On release days, waits until the official release time, then launches `eia_stats.py --poll`.

Calendar times are in New York/Eastern time. Normal runs do not wait until 10:30 a.m. Eastern (9:30 a.m. Central).

## Windows Task Scheduler

Use this as the task action after extracting the folder. The script resolves paths relative to its own folder, so the package can live anywhere. Set the task's working directory to that folder, or select the script's full path in Task Scheduler. Choose a trigger near the intended fetch time: the default run will not wait until the scheduled EIA release.

Program/script:

```powershell
powershell.exe
```

Add arguments:

```powershell
-NoProfile -ExecutionPolicy Bypass -File ".\run_eia_stats_task.ps1"
```

The PowerShell runner is self-contained: it moves into this folder, creates `.venv` if needed, installs `requirements.txt` when needed, immediately fetches live data with up to 120 attempts 0.4 seconds apart, writes `eia_stats.png`, updates `eia_stats_status.json`, archives the dated image, and logs to `logs\`.

`-Latest` and `-IgnoreSchedule` remain compatibility aliases for the default immediate behavior. Only `-Scheduled` restores calendar waiting and scheduled duplicate protection. Direct Python use supports `--poll --latest`, with the same 0.4-second/120-attempt defaults. Exhausting the attempt limit or an optional time limit returns a nonzero exit code.

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
-Scheduled
-MaxAttempts 120
-IntervalSeconds 0.4
-DurationSeconds 60
-NoPreview
-NoClipboard
```

- `-ShowDecision` prints the immediate-run settings without fetching data; add `-Scheduled` to inspect the calendar instead.
- `-RefreshScheduleOnly` updates the cached EIA schedule and exits.
- `-IgnoreSchedule` is a compatibility alias for the default immediate run.
- `-Scheduled` explicitly enables the legacy calendar/release-time wait.
- `-MaxAttempts 120` controls the total number of attempts.
- `-IntervalSeconds 0.4` controls the pause between completed attempts.
- `-DurationSeconds 60` adds a 60-second limit; the default is no separate time limit.
- `-NoPreview` skips opening `eia_stats.png` after generation.
- `-NoClipboard` skips copying `eia_stats.png` to the clipboard.

## Configuration

Environment variables supported by the recreated script:

```bash
export EIA_STATS_OUTPUT_PATH=eia_stats.png
export EIA_STATS_STATUS_FILE=eia_stats_status.json
export EIA_STATS_REFRESH_INTERVAL_SECONDS=0.4
export EIA_STATS_MAX_ATTEMPTS=120
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
- An immediate live-data runner with optional calendar-aware scheduling.
- Pillow direct table drawing.
- Persistent Windows bitmap clipboard copying through `pywin32`, or macOS `osascript` clipboard copying.
