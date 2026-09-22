#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import contextmanager
from functools import lru_cache
import json
import os
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from shutil import copy2
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont


BASE_DIR = Path(__file__).resolve().parent
URL_WPSR_PRODUCTION = "https://ir.eia.gov/wpsr/wpsr.json"

DEFAULT_OUTPUT_PATH = "eia_stats.png"
DEFAULT_STATUS_FILE = "eia_stats_status.json"
MIN_POLL_INTERVAL_SECONDS = 0.25
CREDIT_TEXT = "Created by: Alex Hoffmann"
DATE_HEADER_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")
NON_NUMERIC_RE = re.compile(r"[^0-9.\-]")


def read_env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def read_env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def resolve_path(raw_path: str | None, default: str) -> Path:
    path = Path(raw_path or default).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def resolve_output_path() -> Path:
    return resolve_path(os.environ.get("EIA_STATS_OUTPUT_PATH"), DEFAULT_OUTPUT_PATH)


def resolve_status_path() -> Path:
    return resolve_path(os.environ.get("EIA_STATS_STATUS_FILE"), DEFAULT_STATUS_FILE)


def clean_number(value: Any) -> float:
    text = "" if value is None else str(value).strip()
    text = text.replace(",", "")
    text = NON_NUMERIC_RE.sub("", text)
    if not text or text in {"-", ".", "-."}:
        return 0.0
    return float(text)


def fmt_stock(value: float) -> str:
    return f"{value:,.1f}"


def fmt_change(value: float) -> str:
    if value < 0:
        return f"({abs(value):.3f})"
    return f"{value:.3f}"


def parse_header_date(text: str) -> date:
    month, day, year = [int(part) for part in text.split("/")]
    year += 2000 if year < 100 else 0
    return date(year, month, day)


def parse_json_date(text: str) -> date:
    if not text.strip():
        raise ValueError("Missing date in WPSR JSON")
    return parse_header_date(text.strip())


def json_date_keys(day: date) -> list[str]:
    return [f"{day.month}/{day.day}/{day.year % 100:02d}", f"{day.month}/{day.day}/{day.year}"]


def most_recent_friday(today: date | None = None) -> date:
    today = today or date.today()
    return today - timedelta(days=(today.weekday() - 4) % 7)


def next_friday(after: date) -> date:
    return after + timedelta(days=((4 - after.weekday()) % 7) or 7)


def load_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"generated": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"generated": []}
    if not isinstance(data, dict) or not isinstance(data.get("generated"), list):
        return {"generated": []}
    return data


def save_status(path: Path, status: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


@contextmanager
def status_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def history_dates(status: dict[str, Any]) -> set[str]:
    return {
        str(item.get("date"))
        for item in status.get("generated", [])
        if isinstance(item, dict) and item.get("date")
    }


def target_friday(status: dict[str, Any]) -> date:
    dates: list[date] = []
    for item in status.get("generated", []):
        if not isinstance(item, dict) or not item.get("date"):
            continue
        try:
            dates.append(date.fromisoformat(str(item["date"])))
        except ValueError:
            pass
    if dates:
        return next_friday(max(dates))
    return most_recent_friday()


def wpsr_url(today: date | None = None) -> str:
    override = os.environ.get("EIA_STATS_WPSR_URL", "").strip()
    if override:
        return override
    return URL_WPSR_PRODUCTION


def request_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json,*/*",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "User-Agent": "eia-stats/1.0",
    }
    if extra:
        headers.update(extra)
    return headers


def build_http_timeout(timeout: float) -> httpx.Timeout:
    return httpx.Timeout(timeout, connect=timeout, read=timeout, write=timeout, pool=timeout)


def make_http_client(timeout: float) -> httpx.Client:
    return httpx.Client(
        http2=True,
        follow_redirects=True,
        headers=request_headers(),
        timeout=build_http_timeout(timeout),
    )


def fetch_wpsr_json(timeout: float, client: httpx.Client | None = None) -> dict[str, Any]:
    active_client = client or make_http_client(timeout)
    close_client = client is None
    try:
        response = active_client.get(wpsr_url())
        response.raise_for_status()
        data = response.json()
    finally:
        if close_client:
            active_client.close()
    if not isinstance(data, dict) or not isinstance(data.get("series"), list):
        raise ValueError("Unexpected WPSR JSON response")
    if len(data["series"]) < 100:
        raise ValueError("Short WPSR JSON response")
    return data


def fetch_release_probe(client: httpx.Client, timeout: float) -> tuple[date, FetchResult]:
    start = time.perf_counter()
    data = fetch_wpsr_json(timeout, client=client)
    fetched = FetchResult(data=data, elapsed_seconds=time.perf_counter() - start)
    return parse_json_date(str(data.get("current_week", ""))), fetched


@dataclass(frozen=True)
class FetchResult:
    data: dict[str, Any]
    elapsed_seconds: float


def fetch_wpsr(timeout: float) -> FetchResult:
    start = time.perf_counter()
    fetch_attempts = max(1, read_env_int("EIA_STATS_IMAGE_FETCH_RETRY_ATTEMPTS", 3))
    fetch_delay = max(0.0, read_env_float("EIA_STATS_IMAGE_FETCH_RETRY_SECONDS", 0.25))
    last_error: Exception | None = None
    for attempt in range(1, fetch_attempts + 1):
        try:
            return FetchResult(data=fetch_wpsr_json(timeout), elapsed_seconds=time.perf_counter() - start)
        except Exception as exc:
            last_error = exc
            if attempt < fetch_attempts and fetch_delay:
                time.sleep(fetch_delay)
    raise RuntimeError(f"Could not fetch EIA WPSR JSON data: {last_error}")


@dataclass(frozen=True)
class StatsTable:
    title: str
    rows: list[tuple[str, float, float]]


@dataclass(frozen=True)
class SeriesMapping:
    label: str
    sourcekey: str


GASOLINE_SERIES = [
    SeriesMapping("Total US:", "WGTSTUS1"),
    SeriesMapping("Padd 1:", "WGTSTP11"),
    SeriesMapping("1a:", "WGTST1A1"),
    SeriesMapping("1b:", "WGTST1B1"),
    SeriesMapping("1c:", "WGTST1C1"),
    SeriesMapping("Padd 2:", "WGTSTP21"),
    SeriesMapping("Padd 3:", "WGTSTP31"),
    SeriesMapping("Padd 4:", "WGTSTP41"),
    SeriesMapping("Padd 5:", "WGTSTP51"),
]

DISTILLATE_SERIES = [
    SeriesMapping("Total US:", "WDISTUS1"),
    SeriesMapping("Padd 1:", "WDISTP11"),
    SeriesMapping("1a:", "WDIST1A1"),
    SeriesMapping("1b:", "WDIST1B1"),
    SeriesMapping("1c:", "WDIST1C1"),
    SeriesMapping("Padd 2:", "WDISTP21"),
    SeriesMapping("Padd 3:", "WDISTP31"),
    SeriesMapping("Padd 4:", "WDISTP41"),
    SeriesMapping("Padd 5:", "WDISTP51"),
]

JET_SERIES = [
    SeriesMapping("Total US:", "WKJSTUS1"),
    SeriesMapping("Padd 1:", "WKJSTP11"),
    SeriesMapping("Padd 2:", "WKJSTP21"),
    SeriesMapping("Padd 3:", "WKJSTP31"),
    SeriesMapping("Padd 4:", "WKJSTP41"),
    SeriesMapping("Padd 5:", "WKJSTP51"),
]

CRUDE_SERIES = [
    SeriesMapping("Total US:", "WCESTUS1"),
    SeriesMapping("Padd 1:", "WCESTP11"),
    SeriesMapping("Padd 2:", "WCESTP21"),
    SeriesMapping("Padd 3:", "WCESTP31"),
    SeriesMapping("Padd 4:", "WCESTP41"),
    SeriesMapping("Padd 5:", "WCESTP51"),
    SeriesMapping("Cushing:", "W_EPC0_SAX_YCUOK_MBBL"),
    SeriesMapping("SPR:", "WCSSTUS1"),
]


def series_by_sourcekey(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for item in data.get("series", []):
        if not isinstance(item, dict):
            continue
        sourcekey = str(item.get("sourcekey", "")).strip()
        if sourcekey:
            rows[sourcekey] = item
    return rows


def value_for_date(row: dict[str, Any], day: date) -> float:
    for key in json_date_keys(day):
        if key in row:
            return clean_number(row[key])
    for key, value in row.items():
        if DATE_HEADER_RE.fullmatch(str(key)):
            try:
                if parse_header_date(str(key)) == day:
                    return clean_number(value)
            except ValueError:
                pass
    sourcekey = row.get("sourcekey", "<unknown>")
    raise ValueError(f"Could not find {day.isoformat()} value for {sourcekey}")


def rows_from_mappings(
    by_sourcekey: dict[str, dict[str, Any]],
    mappings: list[SeriesMapping],
    current_week: date,
    previous_week: date,
) -> list[tuple[str, float, float]]:
    rows: list[tuple[str, float, float]] = []
    for mapping in mappings:
        row = by_sourcekey.get(mapping.sourcekey)
        if row is None:
            raise ValueError(f"Could not find WPSR series sourcekey: {mapping.sourcekey}")
        current = value_for_date(row, current_week) / 1000
        previous = value_for_date(row, previous_week) / 1000
        rows.append((mapping.label, current - previous, current))
    return rows


def build_stats(data: dict[str, Any]) -> tuple[date, list[StatsTable]]:
    current_week = parse_json_date(str(data.get("current_week", "")))
    previous_week = parse_json_date(str(data.get("week_ago", "")))
    by_sourcekey = series_by_sourcekey(data)

    return current_week, [
        StatsTable("Gasoline", rows_from_mappings(by_sourcekey, GASOLINE_SERIES, current_week, previous_week)),
        StatsTable("Distillates", rows_from_mappings(by_sourcekey, DISTILLATE_SERIES, current_week, previous_week)),
        StatsTable("Jet", rows_from_mappings(by_sourcekey, JET_SERIES, current_week, previous_week)),
        StatsTable("Crude", rows_from_mappings(by_sourcekey, CRUDE_SERIES, current_week, previous_week)),
    ]


@lru_cache(maxsize=None)
def load_font(size: int, bold: bool = False, italic: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/arialbi.ttf" if bold and italic else "",
        "C:/Windows/Fonts/ariali.ttf" if italic else "",
        "/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf" if bold and italic else "",
        "/System/Library/Fonts/Supplemental/Arial Italic.ttf" if italic else "",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/Arialbd.ttf" if bold else "C:/Windows/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold_Italic.ttf" if bold and italic else "",
        "/usr/share/fonts/truetype/msttcorefonts/Arial_Italic.ttf" if italic else "",
        "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf" if bold else "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf" if bold and italic else "",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf" if italic else "",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/ArialHB.ttc",
        "/System/Library/Fonts/Menlo.ttc",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def draw_cell(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    align: str = "left",
) -> None:
    x1, y1, x2, y2 = xy
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    if align == "right":
        x = x2 - width - 10
    elif align == "center":
        x = x1 + (x2 - x1 - width) // 2
    else:
        x = x1 + 10
    y = y1 + (y2 - y1 - height) // 2
    draw.text((x - bbox[0], y - bbox[1]), text, fill=fill, font=font)


def draw_credit(draw: ImageDraw.ImageDraw, image_width: int, image_height: int) -> None:
    font = load_font(12, italic=True)
    bbox = draw.textbbox((0, 0), CREDIT_TEXT, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad_x = 8
    pad_y = 4
    x2 = image_width - 12
    y2 = image_height - 8
    x1 = x2 - text_w - pad_x * 2
    y1 = y2 - text_h - pad_y * 2
    draw.rounded_rectangle([x1, y1, x2, y2], radius=4, fill=(238, 237, 230), outline=(222, 220, 213))
    draw.text((x1 + pad_x - bbox[0], y1 + pad_y - bbox[1]), CREDIT_TEXT, fill=(112, 106, 108), font=font)


def render_image(tables: list[StatsTable], release_date: date, output_path: Path) -> None:
    width = 1300
    height = 280
    table_y = 50
    row_h = 22
    header_h = 24

    bg = (250, 249, 242)
    cell_bg = (250, 249, 242)
    total_row_bg = (220, 221, 211)
    grid = (58, 50, 54)
    text = (35, 32, 34)
    neg = (222, 48, 40)

    image = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(image)
    title_font = load_font(18, bold=True)
    header_font = load_font(15, bold=True)
    cell_font = load_font(14)
    cell_bold_font = load_font(14, bold=True)
    small_font = load_font(13, bold=True)

    specs = [
        (28, 282, 86, 98, 98),
        (348, 292, 92, 100, 100),
        (678, 264, 82, 92, 90),
        (980, 292, 92, 100, 100),
    ]

    def draw_box(x1: int, y1: int, x2: int, y2: int, fill: tuple[int, int, int] = cell_bg) -> None:
        draw.rectangle([x1, y1, x2, y2], fill=fill, outline=grid, width=2)

    for table, (x, _table_w, label_w, change_w, stock_w) in zip(tables, specs, strict=True):
        title_y = 10
        change_x = x + label_w
        stock_x = change_x + change_w
        draw_cell(draw, (change_x, title_y, stock_x + stock_w, title_y + 26), table.title, title_font, text, "center")

        draw_box(change_x, table_y, stock_x, table_y + header_h)
        draw_box(stock_x, table_y, stock_x + stock_w, table_y + header_h)
        draw_cell(draw, (change_x, table_y, stock_x, table_y + header_h), "\u0394 w/w", header_font, text, "center")
        draw_cell(draw, (stock_x, table_y, stock_x + stock_w, table_y + header_h), "Stocks", header_font, text, "center")

        for idx, (label, change, stock) in enumerate(table.rows):
            ry = table_y + header_h + row_h * idx
            row_fill = total_row_bg if label == "Total US:" else cell_bg
            draw_box(x, ry, change_x, ry + row_h, row_fill)
            draw_box(change_x, ry, stock_x, ry + row_h, row_fill)
            draw_box(stock_x, ry, stock_x + stock_w, ry + row_h, row_fill)
            is_subrow = label in {"1a:", "1b:", "1c:"}
            row_font = cell_font if is_subrow else cell_bold_font
            draw_cell(draw, (x, ry, change_x, ry + row_h), label, row_font, text, "center")
            draw_cell(
                draw,
                (change_x, ry, stock_x, ry + row_h),
                fmt_change(change),
                row_font,
                neg if change < 0 else text,
                "center",
            )
            draw_cell(
                draw,
                (stock_x, ry, stock_x + stock_w, ry + row_h),
                fmt_stock(stock),
                row_font,
                text,
                "center",
            )

    we_text = f"w/e {release_date.month}/{release_date.day}/{str(release_date.year)[2:]}"
    draw_cell(draw, (12, 4, 170, 28), we_text, small_font, text, "left")
    draw_credit(draw, width, height)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, "PNG", compress_level=1)


def copy_image_to_clipboard(path: Path) -> bool:
    system = platform.system()
    try:
        if system == "Darwin":
            escaped_path = str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"')
            script = f'set the clipboard to (read (POSIX file "{escaped_path}") as JPEG picture)'
            subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True)
            return True
        if system == "Windows":
            script = """
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$image = [System.Drawing.Image]::FromFile($args[0])
try {
    [System.Windows.Forms.Clipboard]::SetDataObject($image, $true)
    Start-Sleep -Milliseconds 100
    if (-not [System.Windows.Forms.Clipboard]::ContainsImage()) {
        throw "Clipboard does not contain an image after copy."
    }
}
finally {
    $image.Dispose()
}
"""
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", script, str(path.resolve())],
                check=True,
                capture_output=True,
                text=True,
            )
            return True
        print(f"Clipboard copy skipped: {system} is not supported by this script.", file=sys.stderr)
        return False
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Clipboard copy failed: {exc}", file=sys.stderr)
        return False


def open_image_preview(path: Path) -> bool:
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(str(path.resolve()))  # type: ignore[attr-defined]
            return True
        if system == "Darwin":
            subprocess.Popen(["open", str(path.resolve())])
            return True
        subprocess.Popen(["xdg-open", str(path.resolve())])
        return True
    except OSError as exc:
        print(f"Image preview failed: {exc}", file=sys.stderr)
        return False


def _create_output_locked(
    output_path: Path,
    status_path: Path,
    no_clipboard: bool,
    no_preview: bool,
    force: bool,
    timeout: float,
    target: date | None = None,
    require_target: bool = True,
    fetched: FetchResult | None = None,
) -> tuple[bool, str]:
    status = load_status(status_path)
    expected = target or target_friday(status)
    expected_key = expected.isoformat()
    seen_dates = history_dates(status)
    if not force and target is not None and expected_key in seen_dates:
        return False, f"Already generated {expected_key}; skipping duplicate"
    if require_target and not force and target is None and seen_dates and date.today() < expected:
        return False, f"Already generated through {max(seen_dates)}; next target is {expected_key}"

    fetched = fetched or fetch_wpsr(timeout)
    release_date, tables = build_stats(fetched.data)
    release_key = release_date.isoformat()

    if not force and release_key in seen_dates:
        return False, f"Already generated {release_key}; skipping duplicate"
    if require_target and release_date < expected:
        return False, f"Data not ready: latest {release_key}, waiting for {expected.isoformat()}"

    start = time.perf_counter()
    render_image(tables, release_date, output_path)
    image_seconds = time.perf_counter() - start

    preview_ok = False
    if not no_preview:
        preview_ok = open_image_preview(output_path)

    clipboard_ok = False
    if not no_clipboard:
        attempts = read_env_int("EIA_STATS_CLIPBOARD_RETRY_ATTEMPTS", 5)
        delay = read_env_float("EIA_STATS_CLIPBOARD_RETRY_SECONDS", 0.25)
        for attempt in range(1, attempts + 1):
            if copy_image_to_clipboard(output_path):
                clipboard_ok = True
                break
            if attempt < attempts:
                time.sleep(delay)

    archive_dir = output_path.parent / "archive" / "historical_outputs"
    archive_dir.mkdir(parents=True, exist_ok=True)
    copy_path = archive_dir / f"{output_path.stem}_{release_key}{output_path.suffix}"
    if copy_path != output_path:
        copy2(output_path, copy_path)

    generated = status.setdefault("generated", [])
    generated.append(
        {
            "date": release_key,
            "output": str(output_path),
            "archive": str(copy_path),
            "clipboard": clipboard_ok,
            "preview": preview_ok,
            "json_fetch_seconds": round(fetched.elapsed_seconds, 4),
            "image_seconds": round(image_seconds, 4),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    status["last_date"] = release_key
    status["last_output"] = str(output_path)
    save_status(status_path, status)

    return True, (
        f"Generated {output_path} for {release_key} "
        f"(json {fetched.elapsed_seconds:.3f}s, image {image_seconds:.3f}s, "
        f"clipboard={clipboard_ok}, preview={preview_ok})"
    )


def create_output(
    output_path: Path,
    status_path: Path,
    no_clipboard: bool,
    no_preview: bool,
    force: bool,
    timeout: float,
    target: date | None = None,
    require_target: bool = True,
    fetched: FetchResult | None = None,
) -> tuple[bool, str]:
    with status_lock(status_path):
        return _create_output_locked(
            output_path=output_path,
            status_path=status_path,
            no_clipboard=no_clipboard,
            no_preview=no_preview,
            force=force,
            timeout=timeout,
            target=target,
            require_target=require_target,
            fetched=fetched,
        )


def poll(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.duration
    attempt = 0
    last_message = ""
    status = load_status(args.status_file)
    expected = args.target_date or target_friday(status)
    expected_key = expected.isoformat()
    if not args.force and expected_key in history_dates(status):
        print(f"Already generated {expected_key}; skipping duplicate")
        return 2

    with make_http_client(args.timeout) as client:
        while time.monotonic() <= deadline:
            attempt += 1
            loop_started = time.monotonic()
            try:
                probe_date, fetched = fetch_release_probe(client, args.timeout)
                if not args.force and probe_date.isoformat() in history_dates(load_status(args.status_file)):
                    message = (
                        f"Latest {probe_date.isoformat()} already generated; "
                        f"waiting for {expected_key}"
                    )
                    generated = False
                elif probe_date < expected:
                    message = (
                        f"Latest {probe_date.isoformat()}, "
                        f"waiting for {expected_key}"
                    )
                    generated = False
                else:
                    generated, message = create_output(
                        output_path=args.output,
                        status_path=args.status_file,
                        no_clipboard=args.no_clipboard,
                        no_preview=args.no_preview,
                        force=args.force,
                        timeout=args.timeout,
                        target=expected,
                        fetched=fetched,
                    )
            except Exception as exc:
                message = f"Attempt {attempt} failed: {exc}"
                generated = False

            if message != last_message:
                print(message, flush=True)
                last_message = message
            if generated:
                return 0

            sleep_for = args.interval - (time.monotonic() - loop_started)
            if sleep_for > 0:
                time.sleep(sleep_for)
    print(f"Timeout after {attempt} attempts; no new stats generated.", file=sys.stderr)
    return 2


def parse_args() -> argparse.Namespace:
    default_interval = read_env_float("EIA_STATS_REFRESH_INTERVAL_SECONDS", 0.25)
    default_attempts = read_env_int("EIA_STATS_MAX_ATTEMPTS", 240)
    parser = argparse.ArgumentParser(description="Fast EIA WPSR petroleum JSON-to-image generator.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Fetch once and generate if data is new.")
    mode.add_argument("--poll", action="store_true", help="Poll until new data is found or duration expires.")
    parser.add_argument("--output", type=Path, default=resolve_output_path(), help="Output PNG path.")
    parser.add_argument("--status-file", type=Path, default=resolve_status_path(), help="History/status JSON path.")
    parser.add_argument("--interval", type=float, default=default_interval)
    parser.add_argument("--duration", type=float, default=default_interval * default_attempts, help="Polling duration in seconds.")
    parser.add_argument("--timeout", type=float, default=read_env_float("EIA_STATS_REQUEST_TIMEOUT_SECONDS", 2.5))
    parser.add_argument("--target-date", type=date.fromisoformat, help="Expected Friday date, YYYY-MM-DD.")
    parser.add_argument("--latest", action="store_true", help="Generate the latest available WPSR week instead of requiring the next Friday target.")
    parser.add_argument("--force", action="store_true", help="Regenerate even if release date is already in history.")
    parser.add_argument("--no-clipboard", action="store_true", help="Do not copy the generated image to the clipboard.")
    parser.add_argument("--no-preview", action="store_true", help="Do not open the generated image preview.")
    args = parser.parse_args()
    if not args.once and not args.poll:
        run_mode = os.environ.get("EIA_STATS_RUN_MODE", "once").strip().lower()
        args.poll = run_mode == "poll"
        args.once = not args.poll
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if args.poll and args.interval < MIN_POLL_INTERVAL_SECONDS:
        parser.error(f"--interval must be at least {MIN_POLL_INTERVAL_SECONDS} seconds in poll mode")
    if args.poll and args.latest:
        parser.error("--latest is only valid with --once")
    if args.duration <= 0:
        parser.error("--duration must be positive")
    return args


def main() -> int:
    args = parse_args()
    if args.poll:
        return poll(args)
    try:
        generated, message = create_output(
            output_path=args.output,
            status_path=args.status_file,
            no_clipboard=args.no_clipboard,
            no_preview=args.no_preview,
            force=args.force,
            timeout=args.timeout,
            target=args.target_date,
            require_target=not args.latest,
        )
    except Exception as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1
    print(message)
    return 0 if generated else 2


if __name__ == "__main__":
    raise SystemExit(main())
