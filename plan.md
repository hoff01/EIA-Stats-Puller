# Petroleum Stats Image Rebuild Plan

## Objective

Recreate the Python stats workflow shown in the provided photos as a self-contained, easy-to-run tool that pulls EIA WPSR petroleum JSON data, creates a stats image that closely matches the original output, and copies that image to the clipboard as quickly as possible after release.

## Explicit Requirements Captured So Far

1. Recreate the code from the images, especially the first image that shows imports, URLs, environment variables, helper functions, and setup.
2. Do not include natural gas storage logic.
3. Use the petroleum/WPSR JSON source:
   - `https://ir.eia.gov/wpsr/wpsr.json`
4. Output should be an image, and the image is critical to be as identical as possible to the original.
5. The output image must be copied to the clipboard automatically.
6. Time is critical: the tool must pull data and create/copy the image as soon as new data is available.
7. Avoid browser zoom and avoid Selenium when possible.
8. Optimize the pipeline from JSON retrieval to image creation as much as practical.
9. Make the tool easy to run.
10. Keep everything contained in this folder.
11. Use Python unless another language is clearly faster or simpler; Rust is optional, not required.
12. Include Friday release logic:
    - EIA stats are expected on Friday.
    - Search for the next Friday target when appropriate.
    - Do not repeat stats if the release/date is already in history.
13. Poll aggressively around release:
    - Attempt to pull stats every 0.25 seconds.
    - Continue polling for up to 1 minute.
14. Persist status/history so duplicate output is skipped.
15. Preserve the visible configurable environment variables where practical:
    - `EIA_STATS_OUTPUT_PATH`
    - `EIA_STATS_STATUS_FILE`
    - `EIA_STATS_REFRESH_INTERVAL_SECONDS`
    - `EIA_STATS_MAX_ATTEMPTS`
    - `EIA_STATS_RUN_MODE`
    - `EIA_STATS_REQUEST_TIMEOUT_SECONDS`
    - `EIA_STATS_IMAGE_FETCH_RETRY_ATTEMPTS`
    - `EIA_STATS_IMAGE_FETCH_RETRY_SECONDS`
    - `EIA_STATS_CLIPBOARD_RETRY_ATTEMPTS`
    - `EIA_STATS_CLIPBOARD_RETRY_SECONDS`

## Recreated Data Requirements

The visible code builds tables for:

1. Motor gasoline stocks using WPSR sourcekeys.
2. Distillate fuel oil stocks using WPSR sourcekeys.
3. Kerosene-type jet fuel stocks using WPSR sourcekeys.
4. Crude oil stocks using WPSR sourcekeys.

For each table, calculate and display:

1. Week-over-week change (`w/w`).
2. Current stocks value.

## Performance Plan

1. Use direct HTTP GET requests with a persistent `requests.Session`.
2. Fetch `wpsr.json` with short timeouts and retry handling.
3. Parse JSON from memory and map required rows by stable `sourcekey`; avoid writing intermediate files.
4. Avoid Selenium, screenshots, browser rendering, and zoom manipulation.
5. Use Pillow `ImageDraw` for direct table image rendering instead of Matplotlib.
6. Reuse compact extraction functions and fixed row labels to avoid heavy searching.
7. Copy directly to macOS clipboard with AppleScript/`osascript` after image creation.
8. Use status file checks before and after generation to avoid duplicate work.
9. In poll mode, check the JSON `current_week` before rendering so quarter-second polling stays simple and consistent with the new feed.
10. Keep the minimum polling interval at 0.25 seconds to reduce IP-block risk.

## Package Documentation Checked

Current package usage was checked with Context7:

1. Requests: reuse simple HTTP settings and short timeouts for release polling.
2. Pillow: `ImageDraw.Draw`, `rectangle`, `text`, `textbbox`, `ImageFont.truetype`, and PNG saving are current APIs for direct image rendering.
3. Requests: `requests.Session` reuses TCP connections and supports `get(..., timeout=...)`, `raise_for_status()`, and `response.content`.

## Deliverables

1. `eia_stats.py`: self-contained runnable Python script using Polars, Pillow, and Requests.
2. `requirements.txt`: minimal Python dependency list.
3. `README.md`: exact run commands, environment variable examples, and release polling behavior.
4. `plan.md`: this requirements and execution checklist.
5. Generated output image path defaults to `eia_stats.png`.
6. Status/history path defaults to `eia_stats_status.json`.

## Implementation Checklist

1. Create optimized direct JSON fetch code.
2. Implement robust JSON date extraction from metadata and date-keyed series fields.
3. Implement Friday target/date logic.
4. Implement duplicate history detection.
5. Implement 0.25-second polling for up to 1 minute.
6. Implement table extraction for gasoline, distillate, jet, and crude.
7. Implement Pillow table image styling close to the original.
8. Implement macOS clipboard copy for PNG output.
9. Add CLI flags for one-shot run, polling, no-clipboard mode, and output path.
10. Add clear errors and status messages.
11. Add a smoke-test mode using live JSON downloads.
12. Verify syntax and run at least one end-to-end image generation.
13. Verify duplicate detection skips already-produced data.
14. Verify clipboard copy command works or reports a clear warning.

## Completion Criteria

The work is complete only when:

1. `python3 eia_stats.py --once --no-clipboard` creates an image from live EIA petroleum JSON.
2. `python3 eia_stats.py --poll --interval 0.25 --duration 60 --no-clipboard` can poll without natural gas code.
3. A second run for the same release can skip duplicate generation when history is enabled.
4. The generated image contains the four petroleum sections: Gasoline, Distillate, Jet, and Crude.
5. The tool has no pandas, Selenium, Matplotlib, or browser zoom dependency.
6. Run instructions are present and accurate.
7. No natural gas storage source or parser exists in the implementation.
