# EIA Stats Puller

Standalone private repository: https://github.com/hoff01/EIA-Stats-Puller.
The PDF dashboard is separate at https://github.com/hoff01/EIA-Summary.
Extract anywhere, for example `%USERPROFILE%\Documents\EIA-Stats-Puller`.
All paths resolve relative to this folder. The Windows runner creates its own
environment; do not copy `.venv` from another computer. Requirements include
`python-certifi-win32` on Windows to include trusted Windows certificates.

This repo now contains two fully contained runnable variants:

- `old_stats/`: legacy CSV path using the public Weekly Petroleum Status Report tables. This is the version that works live right now.
- `new_stats/`: newer JSON path using `https://ir.eia.gov/wpsr/wpsr.json`. This is packaged and runnable, but as of June 7, 2026 the live endpoint is returning `403 Forbidden`.

The top-level `eia_stats.py`, `requirements.txt`, and `run_eia_stats_task.ps1` now delegate to `old_stats/` so older shortcuts and copied Task Scheduler actions use the current live path.

Both variants now also include a daily schedule-aware runner driven by the official EIA release calendar at `https://www.eia.gov/petroleum/supply/weekly/schedule.php`.

## Quick Run

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
