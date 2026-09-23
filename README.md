# EIA Stats Puller

Standalone public repository: https://github.com/hoff01/EIA-Stats-Puller.
The PDF dashboard is separate at https://github.com/hoff01/EIA-Summary.
Extract anywhere, for example `%USERPROFILE%\Documents\EIA-Stats-Puller`.
All paths resolve relative to this folder. The Windows runner creates its own
environment; do not copy `.venv` from another computer. Requirements include
`python-certifi-win32` on Windows to include trusted Windows certificates.
Windows clipboard output uses `pywin32` to store a persistent bitmap directly,
without launching a PowerShell clipboard subprocess.
Clipboard access requires an unlocked, interactive Windows session. If Windows
denies access, the script reports `clipboard=False`; the generated PNG remains
available. Run scheduled clipboard tasks only while the user is logged on.
If an image viewer locks the previous PNG, the next result is saved under a
unique dated filename and the preview/status points to that new image.

This repo now contains two fully contained runnable variants:

- `old_stats/`: legacy CSV path using the public Weekly Petroleum Status Report tables. This is the version that works live right now.
- `new_stats/`: newer JSON path using `https://ir.eia.gov/wpsr/wpsr.json`. This is packaged and runnable, but as of June 7, 2026 the live endpoint is returning `403 Forbidden`.

The top-level `eia_stats.py`, `requirements.txt`, and `run_eia_stats_task.ps1` now delegate to `old_stats/` so older shortcuts and copied Task Scheduler actions use the current live path.

Both variants now also include a daily schedule-aware runner driven by the official EIA release calendar at `https://www.eia.gov/petroleum/supply/weekly/schedule.php`.

## Quick Run

**One-button Windows use:** install Python 3.11+ and extract the entire download. Double-click `RUN_EIA_STATS.bat`. It automatically creates the environment, installs dependencies when needed, waits for the scheduled release, fetches the stats, copies the image to the clipboard, opens the preview and archives the result. No separate setup step is required. Keep the Windows session unlocked for clipboard copying. No administrator rights are required. `SETUP_WINDOWS.bat` is an optional setup-only button that does not fetch stats.

The batch files resolve paths relative to their own folder, including paths with spaces, show progress and errors, and preserve exit codes. Double-click runs stay open when finished. Set `EIA_NO_PAUSE=1` for unattended runs. `RUN_EIA_STATS.bat -ShowDecision` checks the schedule without generating an image or changing the clipboard. Optional PowerShell switches such as `-NoPreview` also work through the batch launcher; omit `-NoClipboard` to keep automatic copying enabled.

Legacy CSV version:

```bash
cd old_stats
python3 -m pip install -r requirements.txt
python3 eia_stats.py --once --latest --force
```

JSON version:

```bash
cd new_stats
python3 -m pip install -r requirements.txt
python3 eia_stats.py --once --latest --force
```

Daily Windows-ready runner from the repo root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\run_eia_stats_task.ps1"
```

Direct package runners still work:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\old_stats\run_eia_stats_task.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\new_stats\run_eia_stats_task.ps1"
```

That runner refreshes the EIA schedule weekly, interprets all listed times in New York/Eastern time, exits immediately on non-release days, and on release days waits until the official release time before starting the poll. The standard schedule on the EIA page is `10:30 a.m. Eastern`. It records generated release dates in `eia_stats_status.json` and exits cleanly without republishing a week that was already generated unless `-Force` is supplied.
By default, the live runner writes `old_stats\eia_stats.png`, opens it on screen, copies it to the Windows clipboard for chat pasting, and archives a dated copy. Add `-NoPreview` or `-NoClipboard` only when you intentionally want to skip those actions.

The default release watch checks every 0.5 seconds for up to 120 seconds after the scheduled release time, retrying unavailable, blank, HTML/error or stale responses. Starting early waits for the scheduled time before this window begins. Requests do not overlap: a slow request must finish or time out before the next poll, and no new poll starts at or after the deadline. An in-flight request can finish after the polling window; request timeouts and bounded retries remain enabled. Override `-IntervalSeconds` or `-DurationSeconds` only when needed.

Once validated data is ready, the image is rendered and copied to the clipboard before opening the preview or writing the archive. Clipboard readback verifies the bitmap; a busy clipboard is retried up to five times, 0.25 seconds apart. The default runner enables clipboard copying. Do not use `-NoClipboard` when you want to paste into chats, and run in your unlocked, logged-on Windows session. A `clipboard=False` result means copying failed, even though the PNG was generated.

The CSV runner reuses the polling connection pool when the new release arrives, downloads at most three tables concurrently, and retries only failed downloads within an attempt. It still rejects blank/HTML responses and mixed release dates. The JSON variant reuses its connection pool during retries. These changes reduce connection setup and duplicate requests without polling EIA faster. Both variants retain Windows certificate support, preview, clipboard retries and duplicate-week protection.

Both folders are self-contained and each includes:

- `eia_stats.py`
- `requirements.txt`
- `run_eia_stats_task.ps1`
- `wpsr_schedule_runner.py`
- `wpsr_schedule_seed.json`
- `README.md`

## Current Status

- `old_stats/` is the fastest working path for current live use.
- `new_stats/` remains useful as the isolated JSON implementation if EIA restores access to the JSON feed.

See the folder-specific READMEs for detailed setup, polling, Windows Task Scheduler, and environment-variable options.
