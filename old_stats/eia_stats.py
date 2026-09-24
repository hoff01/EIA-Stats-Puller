#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from contextlib import contextmanager
import io
import json
import os
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from shutil import copy2
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont


BASE_DIR = Path(__file__).resolve().parent
LEGACY_WEEKLY_PAGE_URL = "https://www.eia.gov/petroleum/supply/weekly/"
LEGACY_TABLE_URLS = {
    "table4": "https://ir.eia.gov/wpsr/table4.csv",
    "table5a": "https://ir.eia.gov/wpsr/table5a.csv",
    "table6": "https://ir.eia.gov/wpsr/table6.csv",
}

DEFAULT_OUTPUT_PATH = "eia_stats.png"
DEFAULT_STATUS_FILE = "eia_stats_status.json"
MIN_POLL_INTERVAL_SECONDS = 0.25
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


def legacy_table_url(table_name: str) -> str:
    env_name = f"EIA_STATS_{table_name.upper()}_URL"
    override = os.environ.get(env_name, "").strip()
    if override:
        return override
    return LEGACY_TABLE_URLS[table_name]


def request_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Accept": "text/csv,application/json,*/*",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "User-Agent": "eia-stats/1.0",
        "Referer": LEGACY_WEEKLY_PAGE_URL,
    }
    if extra:
        headers.update(extra)
    return headers


@dataclass(frozen=True)
class CsvTable:
    name: str
    header: list[str]
    rows: list[list[str]]
    stub_count: int
    current_index: int
    previous_index: int

    @property
    def label_index(self) -> int:
        return self.stub_count - 1


def decode_response_bytes(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1", errors="replace")


def parse_csv_table(content: bytes, table_name: str) -> CsvTable:
    text = decode_response_bytes(content)
    leading = text.lstrip()[:200].lower()
    if leading.startswith("<!doctype html") or leading.startswith("<html"):
        raise ValueError(f"{table_name} returned HTML instead of CSV")

    reader = csv.reader(io.StringIO(text))
    rows = [[cell.strip() for cell in row] for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError(f"{table_name} returned no CSV rows")

    header = rows[0]
    stub_count = 0
    for cell in header:
        if cell.startswith("STUB_"):
            stub_count += 1
        else:
            break

    date_indexes = [index for index, cell in enumerate(header) if DATE_HEADER_RE.fullmatch(cell)]
    if stub_count < 1 or len(date_indexes) < 2:
        raise ValueError(f"Unexpected CSV header for {table_name}: {header}")

    return CsvTable(
        name=table_name,
        header=header,
        rows=rows[1:],
        stub_count=stub_count,
        current_index=date_indexes[0],
        previous_index=date_indexes[1],
    )


def build_http_timeout(timeout: float) -> httpx.Timeout:
    return httpx.Timeout(timeout, connect=timeout, read=timeout, write=timeout, pool=timeout)


def make_http_client(timeout: float) -> httpx.Client:
    return httpx.Client(
        http2=True,
        follow_redirects=True,
        headers=request_headers(),
        timeout=build_http_timeout(timeout),
    )


def fetch_csv_table(table_name: str, timeout: float, client: httpx.Client | None = None) -> CsvTable:
    active_client = client or make_http_client(timeout)
    close_client = client is None
    try:
        response = active_client.get(legacy_table_url(table_name))
        response.raise_for_status()
        table = parse_csv_table(response.content, table_name)
    finally:
        if close_client:
            active_client.close()
    return table


def release_dates(table: CsvTable) -> tuple[date, date]:
    return parse_header_date(table.header[table.current_index]), parse_header_date(table.header[table.previous_index])


def fetch_release_probe(client: httpx.Client, timeout: float) -> tuple[date, FetchResult]:
    start = time.perf_counter()
    table4 = fetch_csv_table("table4", timeout, client=client)
    fetched = FetchResult(data={"table4": table4}, elapsed_seconds=time.perf_counter() - start)
    current_week, _ = release_dates(table4)
    return current_week, fetched


@dataclass(frozen=True)
class FetchResult:
    data: dict[str, CsvTable]
    elapsed_seconds: float


def fetch_wpsr(timeout: float, initial_tables: dict[str, CsvTable] | None = None,
               client: httpx.Client | None = None) -> FetchResult:
    start = time.perf_counter()
    fetch_attempts = max(1, read_env_int("EIA_STATS_IMAGE_FETCH_RETRY_ATTEMPTS", 3))
    fetch_delay = max(0.0, read_env_float("EIA_STATS_IMAGE_FETCH_RETRY_SECONDS", 0.25))
    last_error: Exception | None = None
    active_client = client or make_http_client(timeout)
    tables = dict(initial_tables or {})
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            for attempt in range(1, fetch_attempts + 1):
                missing = [name for name in LEGACY_TABLE_URLS if name not in tables]
                futures = {executor.submit(fetch_csv_table, name, timeout, active_client): name for name in missing}
                for future in as_completed(futures):
                    try:
                        tables[futures[future]] = future.result()
                    except Exception as exc:
                        last_error = exc
                if all(name in tables for name in LEGACY_TABLE_URLS):
                    ordered = {name: tables[name] for name in LEGACY_TABLE_URLS}
                    return FetchResult(data=ordered, elapsed_seconds=time.perf_counter() - start)
                if attempt < fetch_attempts and fetch_delay:
                    time.sleep(fetch_delay)
    finally:
        if client is None:
            active_client.close()
    raise RuntimeError(f"Could not fetch EIA WPSR legacy CSV data: {last_error}")


@dataclass(frozen=True)
class StatsTable:
    title: str
    rows: list[tuple[str, float, float]]


@dataclass(frozen=True)
class RowMapping:
    label: str
    source_label: str


GASOLINE_SERIES = [
    RowMapping("Total US:", "Total Motor Gasoline"),
    RowMapping("Padd 1:", "East Coast (PADD 1)"),
    RowMapping("1a:", "New England (PADD 1A)"),
    RowMapping("1b:", "Central Atlantic (PADD 1B)"),
    RowMapping("1c:", "Lower Atlantic (PADD 1C)"),
    RowMapping("Padd 2:", "Midwest (PADD 2)"),
    RowMapping("Padd 3:", "Gulf Coast (PADD 3)"),
    RowMapping("Padd 4:", "Rocky Mountain (PADD 4)"),
    RowMapping("Padd 5:", "West Coast (PADD 5)"),
]

DISTILLATE_SERIES = [
    RowMapping("Total US:", "Distillate Fuel Oil"),
    RowMapping("Padd 1:", "East Coast (PADD 1)"),
    RowMapping("1a:", "New England (PADD 1A)"),
    RowMapping("1b:", "Central Atlantic (PADD 1B)"),
    RowMapping("1c:", "Lower Atlantic (PADD 1C)"),
    RowMapping("Padd 2:", "Midwest (PADD 2)"),
    RowMapping("Padd 3:", "Gulf Coast (PADD 3)"),
    RowMapping("Padd 4:", "Rocky Mountain (PADD 4)"),
    RowMapping("Padd 5:", "West Coast (PADD 5)"),
]

JET_SERIES = [
    RowMapping("Total US:", "Kerosene-Type Jet Fuel"),
    RowMapping("Padd 1:", "East Coast (PADD 1)"),
    RowMapping("Padd 2:", "Midwest (PADD 2)"),
    RowMapping("Padd 3:", "Gulf Coast (PADD 3)"),
    RowMapping("Padd 4:", "Rocky Mountain (PADD 4)"),
    RowMapping("Padd 5:", "West Coast (PADD 5)"),
]

CRUDE_SERIES = [
    RowMapping("Total US:", "Commercial (Excluding SPR)"),
    RowMapping("Padd 1:", "East Coast (PADD 1)"),
    RowMapping("Padd 2:", "Midwest (PADD 2)"),
    RowMapping("Padd 3:", "Gulf Coast (PADD 3)"),
    RowMapping("Padd 4:", "Rocky Mountain (PADD 4)"),
    RowMapping("Padd 5:", "West Coast (PADD 5)"),
    RowMapping("Cushing:", "Cushing"),
    RowMapping("SPR:", "SPR"),
]


def row_label(table: CsvTable, row: list[str]) -> str:
    return row[table.label_index].strip()


def find_section(table: CsvTable, start_label: str, end_label: str | None) -> list[list[str]]:
    start_index: int | None = None
    for index, row in enumerate(table.rows):
        if row_label(table, row) == start_label:
            start_index = index
            break
    if start_index is None:
        raise ValueError(f"Could not find {start_label!r} in {table.name}")

    end_index = len(table.rows)
    if end_label:
        for index in range(start_index + 1, len(table.rows)):
            if row_label(table, table.rows[index]) == end_label:
                end_index = index
                break
    return table.rows[start_index:end_index]


def rows_from_mappings(
    table: CsvTable,
    section_rows: list[list[str]],
    mappings: list[RowMapping],
) -> list[tuple[str, float, float]]:
    lookup: dict[str, list[str]] = {}
    for row in section_rows:
        label = row_label(table, row)
        if label and label not in lookup:
            lookup[label] = row

    rows: list[tuple[str, float, float]] = []
    for mapping in mappings:
        row = lookup.get(mapping.source_label)
        if row is None:
            raise ValueError(f"Could not find CSV row {mapping.source_label!r} in {table.name}")
        current = clean_number(row[table.current_index])
        previous = clean_number(row[table.previous_index])
        rows.append((mapping.label, current - previous, current))
    return rows


def build_stats(data: dict[str, CsvTable]) -> tuple[date, list[StatsTable]]:
    table4 = data["table4"]
    table5a = data["table5a"]
    table6 = data["table6"]
    current_week, previous_week = release_dates(table4)
    for table in (table5a, table6):
        if release_dates(table) != (current_week, previous_week):
            raise ValueError(f"Mismatched release dates in {table.name}")

    gasoline_rows = find_section(table5a, "Total Motor Gasoline", "Finished Motor Gasoline")
    distillate_rows = find_section(table6, "Distillate Fuel Oil", "15 ppm sulfur and Under")
    jet_rows = find_section(table6, "Kerosene-Type Jet Fuel", "Residual Fuel Oil")
    crude_rows = find_section(table4, "Commercial (Excluding SPR)", "Total Motor Gasoline")

    return current_week, [
        StatsTable("Gasoline", rows_from_mappings(table5a, gasoline_rows, GASOLINE_SERIES)),
        StatsTable("Distillates", rows_from_mappings(table6, distillate_rows, DISTILLATE_SERIES)),
        StatsTable("Jet", rows_from_mappings(table6, jet_rows, JET_SERIES)),
        StatsTable("Crude", rows_from_mappings(table4, crude_rows, CRUDE_SERIES)),
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


def render_image(tables: list[StatsTable], release_date: date, output_path: Path) -> Path:
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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    try:
        image.save(temporary, "PNG", compress_level=1)
        try:
            temporary.replace(output_path)
        except PermissionError:
            # Windows image viewers can keep the previous output locked.
            output_path = output_path.with_name(f"{output_path.stem}_{release_date.isoformat()}_{time.time_ns()}{output_path.suffix}")
            temporary.replace(output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return output_path


def copy_image_to_clipboard(path: Path) -> bool:
    system = platform.system()
    try:
        if system == "Darwin":
            escaped_path = str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"')
            script = f'set the clipboard to (read (POSIX file "{escaped_path}") as JPEG picture)'
            subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True)
            return True
        if system == "Windows":
            import win32clipboard
            import pywintypes

            with Image.open(path) as image:
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, format="BMP")
                dib = buffer.getvalue()[14:]
            try:
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
                    if win32clipboard.GetClipboardData(win32clipboard.CF_DIB) != dib:
                        raise OSError("Clipboard bitmap verification failed")
                finally:
                    win32clipboard.CloseClipboard()
            except pywintypes.error as exc:
                raise OSError(str(exc)) from exc
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
    client: httpx.Client | None = None,
) -> tuple[bool, str]:
    status = load_status(status_path)
    expected = target or target_friday(status)
    expected_key = expected.isoformat()
    seen_dates = history_dates(status)
    if not force and target is not None and expected_key in seen_dates:
        return False, f"Already generated {expected_key}; skipping duplicate"
    if require_target and not force and target is None and seen_dates and date.today() < expected:
        return False, f"Already generated through {max(seen_dates)}; next target is {expected_key}"

    fetched = fetch_wpsr(timeout, initial_tables=fetched.data if fetched else None, client=client)
    release_date, tables = build_stats(fetched.data)
    release_key = release_date.isoformat()

    if not force and release_key in seen_dates:
        return False, f"Already generated {release_key}; skipping duplicate"
    if require_target and release_date < expected:
        return False, f"Data not ready: latest {release_key}, waiting for {expected.isoformat()}"

    start = time.perf_counter()
    output_path = render_image(tables, release_date, output_path)
    image_seconds = time.perf_counter() - start

    clipboard_ok = False
    if not no_clipboard:
        attempts = max(1, read_env_int("EIA_STATS_CLIPBOARD_RETRY_ATTEMPTS", 5))
        delay = max(0.0, read_env_float("EIA_STATS_CLIPBOARD_RETRY_SECONDS", 0.25))
        for attempt in range(1, attempts + 1):
            if copy_image_to_clipboard(output_path):
                clipboard_ok = True
                break
            if attempt < attempts:
                time.sleep(delay)

    preview_ok = False
    if not no_preview:
        preview_ok = open_image_preview(output_path)

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
            "csv_fetch_seconds": round(fetched.elapsed_seconds, 4),
            "image_seconds": round(image_seconds, 4),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    status["last_date"] = release_key
    status["last_output"] = str(output_path)
    save_status(status_path, status)

    return True, (
        f"Generated {output_path} for {release_key} "
        f"(csv {fetched.elapsed_seconds:.3f}s, image {image_seconds:.3f}s, "
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
    client: httpx.Client | None = None,
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
            client=client,
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
        while time.monotonic() < deadline:
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
                        client=client,
                    )
            except Exception as exc:
                message = f"Attempt {attempt} failed: {exc}"
                generated = False

            if message != last_message:
                print(message, flush=True)
                last_message = message
            if generated:
                return 0

            now = time.monotonic()
            sleep_for = min(args.interval - (now - loop_started), deadline - now)
            if sleep_for > 0:
                time.sleep(sleep_for)
    print(f"Timeout after {attempt} attempts; no new stats generated.", file=sys.stderr)
    return 2


def parse_args() -> argparse.Namespace:
    default_interval = read_env_float("EIA_STATS_REFRESH_INTERVAL_SECONDS", 0.5)
    default_attempts = read_env_int("EIA_STATS_MAX_ATTEMPTS", 240)
    parser = argparse.ArgumentParser(description="Fast EIA WPSR petroleum legacy-CSV-to-image generator.")
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
