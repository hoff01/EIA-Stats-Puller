#!/usr/bin/env python3
"""Compatibility entry point for the current live EIA stats generator.

The transferable package keeps both implementations:
- old_stats: live legacy CSV workflow
- new_stats: WPSR JSON workflow, kept isolated until the JSON endpoint is live

Running this top-level script delegates to old_stats so older shortcuts and
Task Scheduler actions do not accidentally use a stale implementation.
"""

from __future__ import annotations

import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LIVE_SCRIPT = ROOT / "old_stats" / "eia_stats.py"


if __name__ == "__main__":
    runpy.run_path(str(LIVE_SCRIPT), run_name="__main__")
