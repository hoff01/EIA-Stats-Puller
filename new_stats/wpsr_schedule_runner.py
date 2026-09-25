#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
import time as time_module
from typing import Any
from zoneinfo import ZoneInfo

import httpx


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_STATS_SCRIPT = BASE_DIR / "eia_stats.py"
DEFAULT_OUTPUT = BASE_DIR / "eia_stats.png"
DEFAULT_STATUS_FILE = BASE_DIR / "eia_stats_status.json"
DEFAULT_SCHEDULE_CACHE = BASE_DIR / "wpsr_schedule_cache.json"
DEFAULT_SCHEDULE_SEED = BASE_DIR / "wpsr_schedule_seed.json"
SCHEDULE_URL = "https://www.eia.gov/petroleum/supply/weekly/schedule.php"
EASTERN = ZoneInfo("America/New_York")

DEFAULT_REFRESH_DAYS = 7
DEFAULT_RELEASE_RE = re.compile(
    r"after\s+(?P<time>\d{1,2}:\d{2}\s+[ap]\.m\.)\s+eastern time on\s+(?P<day>[A-Za-z]+)",
    re.IGNORECASE,
)
WEEKDAY_LOOKUP = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "main",
    "header",
    "footer",
    "li",
    "ul",
    "ol",
    "table",
    "thead",
    "tbody",
    "tr",
    "td",
    "th",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "br",
}


@dataclass(frozen=True)
class HolidayException:
    week_ending: date
    default_release_date: date
    release_date: date
    release_day_name: str
    release_time_eastern: str
    holiday: str


@dataclass(frozen=True)
class ScheduleData:
    source_url: str
    fetched_at: datetime
    default_release_day_name: str
    default_release_weekday: int
    default_release_time_eastern: str
    holiday_exceptions: list[HolidayException]


@dataclass(frozen=True)
class ReleaseDecision:
    release_date: date
    release_time_eastern: str
    source: str
    holiday: str | None = None


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)


def request_headers() -> dict[str, str]:
    return {
        "Accept": "text/html,*/*",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "User-Agent": "eia-stats-schedule/1.0",
    }


def build_http_timeout(timeout: float) -> httpx.Timeout:
    return httpx.Timeout(timeout, connect=timeout, read=timeout, write=timeout, pool=timeout)


def make_http_client(timeout: float) -> httpx.Client:
    return httpx.Client(
        http2=True,
        follow_redirects=True,
        headers=request_headers(),
        timeout=build_http_timeout(timeout),
    )


def parse_date_text(value: str) -> date:
    return datetime.strptime(value, "%B %d, %Y").date()


def try_parse_date_text(value: str) -> date | None:
    try:
        return parse_date_text(value)
    except ValueError:
        return None


def parse_time_text(value: str) -> time:
    normalized = value.lower().replace(".", "")
    return datetime.strptime(normalized, "%I:%M %p").time()


def format_local(dt_value: datetime) -> str:
    return dt_value.strftime("%Y-%m-%d %I:%M:%S %p %Z")


def html_to_lines(html: str) -> list[str]:
    extractor = TextExtractor()
    extractor.feed(html)
    text = unescape("".join(extractor.parts))
    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split()).strip()
        if line:
            lines.append(line)
    return lines


def parse_holiday_exceptions(lines: list[str], default_release_weekday: int) -> list[HolidayException]:
    heading_index = next((index for index, line in enumerate(lines) if line == "Holiday Release Schedule"), None)
    if heading_index is None:
        raise ValueError("Could not find the holiday release schedule on the EIA page")

    start_index = None
    for index in range(heading_index + 1, len(lines)):
        if try_parse_date_text(lines[index]) is not None:
            start_index = index
            break
    if start_index is None:
        raise ValueError("Could not find any holiday schedule rows on the EIA page")

    holiday_exceptions: list[HolidayException] = []
    index = start_index
    while index + 4 < len(lines):
        week_ending = try_parse_date_text(lines[index])
        release_date = try_parse_date_text(lines[index + 1])
        release_day_name = lines[index + 2]
        release_time_eastern = lines[index + 3]
        holiday = lines[index + 4]

        if (
            week_ending is None
            or release_date is None
            or release_day_name.lower() not in WEEKDAY_LOOKUP
        ):
            break

        parse_time_text(release_time_eastern)
        days_until_default = (default_release_weekday - week_ending.weekday()) % 7
        if days_until_default == 0:
            days_until_default = 7
        default_release_date = week_ending + timedelta(days=days_until_default)

        holiday_exceptions.append(
            HolidayException(
                week_ending=week_ending,
                default_release_date=default_release_date,
                release_date=release_date,
                release_day_name=release_day_name,
                release_time_eastern=release_time_eastern,
                holiday=holiday,
            )
        )
        index += 5

    if not holiday_exceptions:
        raise ValueError("Found the holiday release schedule section but parsed zero exceptions")
    return holiday_exceptions


def parse_schedule_html(html: str) -> ScheduleData:
    lines = html_to_lines(html)

    default_match = None
    for line in lines:
        default_match = DEFAULT_RELEASE_RE.search(line)
        if default_match:
            break
    if default_match is None:
        raise ValueError("Could not find the default WPSR release schedule on the EIA page")

    default_release_day_name = default_match.group("day")
    default_release_time_eastern = default_match.group("time")
    default_release_weekday = WEEKDAY_LOOKUP[default_release_day_name.lower()]

    holiday_exceptions = parse_holiday_exceptions(lines, default_release_weekday)

    return ScheduleData(
        source_url=SCHEDULE_URL,
        fetched_at=datetime.now(tz=EASTERN),
        default_release_day_name=default_release_day_name,
        default_release_weekday=default_release_weekday,
        default_release_time_eastern=default_release_time_eastern,
        holiday_exceptions=holiday_exceptions,
    )


def schedule_to_json(data: ScheduleData) -> dict[str, Any]:
    return {
        "source_url": data.source_url,
        "fetched_at": data.fetched_at.isoformat(),
        "default_release_day_name": data.default_release_day_name,
        "default_release_weekday": data.default_release_weekday,
        "default_release_time_eastern": data.default_release_time_eastern,
        "holiday_exceptions": [
            {
                "week_ending": item.week_ending.isoformat(),
                "default_release_date": item.default_release_date.isoformat(),
                "release_date": item.release_date.isoformat(),
                "release_day_name": item.release_day_name,
                "release_time_eastern": item.release_time_eastern,
                "holiday": item.holiday,
            }
            for item in data.holiday_exceptions
        ],
    }


def schedule_from_json(payload: dict[str, Any]) -> ScheduleData:
    return ScheduleData(
        source_url=str(payload["source_url"]),
        fetched_at=datetime.fromisoformat(str(payload["fetched_at"])),
        default_release_day_name=str(payload["default_release_day_name"]),
        default_release_weekday=int(payload["default_release_weekday"]),
        default_release_time_eastern=str(payload["default_release_time_eastern"]),
        holiday_exceptions=[
            HolidayException(
                week_ending=date.fromisoformat(str(item["week_ending"])),
                default_release_date=date.fromisoformat(str(item["default_release_date"])),
                release_date=date.fromisoformat(str(item["release_date"])),
                release_day_name=str(item["release_day_name"]),
                release_time_eastern=str(item["release_time_eastern"]),
                holiday=str(item["holiday"]),
            )
            for item in payload.get("holiday_exceptions", [])
        ],
    )


def load_schedule_file(path: Path) -> ScheduleData | None:
    if not path.exists():
        return None
    try:
        return schedule_from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


def save_schedule_file(path: Path, schedule: ScheduleData) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schedule_to_json(schedule), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fetch_schedule(timeout: float) -> ScheduleData:
    with make_http_client(timeout) as client:
        response = client.get(SCHEDULE_URL)
        response.raise_for_status()
    return parse_schedule_html(response.text)


def should_refresh(schedule: ScheduleData | None, now_et: datetime, refresh_days: int) -> bool:
    if schedule is None:
        return True
    age = now_et - schedule.fetched_at.astimezone(EASTERN)
    return age >= timedelta(days=refresh_days)


def resolve_schedule(cache_path: Path, seed_path: Path, timeout: float, refresh_days: int, now_et: datetime) -> ScheduleData:
    cache_schedule = load_schedule_file(cache_path)
    seed_schedule = load_schedule_file(seed_path)
    active_schedule = cache_schedule or seed_schedule

    needs_refresh = cache_schedule is None or should_refresh(active_schedule, now_et, refresh_days)
    if needs_refresh:
        try:
            refreshed = fetch_schedule(timeout)
            save_schedule_file(cache_path, refreshed)
            return refreshed
        except Exception as exc:
            if active_schedule is None:
                raise RuntimeError(f"Could not load or refresh the EIA release schedule: {exc}") from exc
            print(
                f"Schedule refresh failed ({exc}); using cached schedule from "
                f"{active_schedule.fetched_at.astimezone(EASTERN).date().isoformat()}",
                flush=True,
            )
            return active_schedule
    return active_schedule


def release_decision_for_day(schedule: ScheduleData, day_et: date) -> ReleaseDecision | None:
    exceptions_by_release_date = {item.release_date: item for item in schedule.holiday_exceptions}
    overridden_default_dates = {item.default_release_date for item in schedule.holiday_exceptions}

    if day_et in exceptions_by_release_date:
        item = exceptions_by_release_date[day_et]
        return ReleaseDecision(
            release_date=day_et,
            release_time_eastern=item.release_time_eastern,
            source="holiday exception",
            holiday=item.holiday,
        )

    if day_et.weekday() == schedule.default_release_weekday and day_et not in overridden_default_dates:
        return ReleaseDecision(
            release_date=day_et,
            release_time_eastern=schedule.default_release_time_eastern,
            source=f"default {schedule.default_release_day_name} schedule",
        )

    return None


def resolve_now_eastern(raw_now: str | None) -> datetime:
    if raw_now is None:
        return datetime.now(tz=EASTERN)
    parsed = datetime.fromisoformat(raw_now)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=EASTERN)
    return parsed.astimezone(EASTERN)


def build_stats_command(args: argparse.Namespace, *, latest: bool = False, target_date: date | None = None) -> list[str]:
    command = [
        sys.executable,
        str(args.stats_script),
        "--poll",
        "--interval",
        str(args.interval),
        "--duration",
        str(args.duration),
        "--timeout",
        str(args.timeout),
        "--output",
        str(args.output),
        "--status-file",
        str(args.status_file),
    ]
    if args.no_clipboard:
        command.append("--no-clipboard")
    if args.no_preview:
        command.append("--no-preview")
    if args.force:
        command.append("--force")
    if latest:
        command.append("--latest")
    elif target_date is not None:
        command.extend(["--target-date", target_date.isoformat()])
    return command


def run_stats_command(args: argparse.Namespace, *, latest: bool = False, target_date: date | None = None) -> int:
    command = build_stats_command(args, latest=latest, target_date=target_date)
    print(f"Launching stats runner: {' '.join(command)}", flush=True)
    completed = subprocess.run(command, check=False)
    return completed.returncode


def log_schedule_decision(now_et: datetime, decision: ReleaseDecision | None) -> None:
    local_tz = datetime.now().astimezone().tzinfo
    if decision is None:
        print(
            f"No WPSR release scheduled for {now_et.date().isoformat()} Eastern. "
            "Fetching the latest published week.",
            flush=True,
        )
        return

    release_dt_et = datetime.combine(decision.release_date, parse_time_text(decision.release_time_eastern), tzinfo=EASTERN)
    release_dt_local = release_dt_et.astimezone(local_tz)
    details = f"{decision.source}, release at {format_local(release_dt_et)}"
    if decision.holiday:
        details += f", holiday={decision.holiday}"
    print(f"Release day detected: {details} ({format_local(release_dt_local)} local).", flush=True)


def wait_until_release(now_et: datetime, decision: ReleaseDecision) -> None:
    release_dt_et = datetime.combine(decision.release_date, parse_time_text(decision.release_time_eastern), tzinfo=EASTERN)
    now_local = now_et.astimezone()
    release_dt_local = release_dt_et.astimezone(now_local.tzinfo)
    if now_local >= release_dt_local:
        print("Official release time has already passed; starting poll now.", flush=True)
        return

    wait_seconds = max(0.0, (release_dt_local - now_local).total_seconds())
    print(
        f"Waiting until official release time: {format_local(release_dt_et)} Eastern "
        f"({format_local(release_dt_local)} local), {wait_seconds:.0f}s remaining.",
        flush=True,
    )
    time_module.sleep(wait_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Daily EIA WPSR schedule-aware runner. It refreshes the official release "
            "schedule, fetches latest data on non-release days, and waits until the "
            "official Eastern release time on release days."
        )
    )
    parser.add_argument("--stats-script", type=Path, default=DEFAULT_STATS_SCRIPT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--status-file", type=Path, default=DEFAULT_STATUS_FILE)
    parser.add_argument("--schedule-cache", type=Path, default=DEFAULT_SCHEDULE_CACHE)
    parser.add_argument("--schedule-seed", type=Path, default=DEFAULT_SCHEDULE_SEED)
    parser.add_argument("--schedule-refresh-days", type=int, default=DEFAULT_REFRESH_DAYS)
    parser.add_argument("--schedule-timeout", type=float, default=20.0)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--duration", type=float, default=120.0)
    parser.add_argument("--timeout", type=float, default=2.5)
    parser.add_argument("--now-eastern", help="Testing override in ISO format; naive values are interpreted as Eastern time.")
    parser.add_argument("--refresh-only", action="store_true", help="Refresh the cached WPSR schedule and exit.")
    parser.add_argument("--show-decision", action="store_true", help="Print today's release decision and exit.")
    parser.add_argument("--ignore-schedule", "--latest", action="store_true", help="Fetch latest published data immediately, ignoring the calendar and prior output history.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-clipboard", action="store_true")
    parser.add_argument("--no-preview", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    now_et = resolve_now_eastern(args.now_eastern)

    if args.ignore_schedule and not args.refresh_only:
        print("Fetching latest published data without a schedule check.", flush=True)
        return 0 if args.show_decision else run_stats_command(args, latest=True)

    try:
        schedule = resolve_schedule(
            cache_path=args.schedule_cache,
            seed_path=args.schedule_seed,
            timeout=args.schedule_timeout,
            refresh_days=args.schedule_refresh_days,
            now_et=now_et,
        )
    except Exception as exc:
        if args.refresh_only:
            raise
        print(f"Schedule unavailable ({exc}); fetching latest published data.", flush=True)
        return 0 if args.show_decision else run_stats_command(args, latest=True)

    save_schedule_file(args.schedule_cache, schedule)
    print(
        f"Using WPSR schedule fetched {schedule.fetched_at.astimezone(EASTERN).isoformat()} "
        f"from {schedule.source_url}",
        flush=True,
    )

    if args.refresh_only:
        print("Schedule refresh complete.", flush=True)
        return 0

    decision = release_decision_for_day(schedule, now_et.date())
    log_schedule_decision(now_et, decision)

    if args.show_decision:
        return 0
    if decision is None:
        return run_stats_command(args, latest=True)

    wait_until_release(now_et, decision)
    holiday = next((item for item in schedule.holiday_exceptions
                    if item.release_date == decision.release_date), None)
    target_date = holiday.week_ending if holiday else (
        decision.release_date - timedelta(days=(decision.release_date.weekday() - 4) % 7 or 7)
    )
    return run_stats_command(args, target_date=target_date)


if __name__ == "__main__":
    raise SystemExit(main())
