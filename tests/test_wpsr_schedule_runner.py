from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

import old_stats.wpsr_schedule_runner as old_runner
import new_stats.wpsr_schedule_runner as new_runner


SAMPLE_HTML = """
<html>
  <body>
    <h1>Weekly Petroleum Status Report Schedule</h1>
    <p>
      The wpsrsummary.pdf, overview.pdf, and Tables 1-14 in CSV and XLS formats,
      are released to the web site after 10:30 a.m. eastern time on Wednesday.
      All other PDF and HTML files are released to the web site after 1:00 p.m.
      eastern time on Wednesday.
    </p>
    <p>For some weeks that include holidays, releases are delayed by one day.</p>
    <h3>Holiday Release Schedule</h3>
    <p>
      The standard release time and day of the week will be at 10:30 a.m. eastern time
      on Wednesdays with the following exceptions. All times are eastern.
    </p>
    <table>
      <tr>
        <th>Data for the week ending</th>
        <th>Alternate release date</th>
        <th>Release day</th>
        <th>Release time</th>
        <th>Holiday</th>
      </tr>
      <tr>
        <td>December 27, 2024</td>
        <td>January 2, 2025</td>
        <td>Thursday</td>
        <td>11:00 a.m.</td>
        <td>New Year's Day</td>
      </tr>
      <tr>
        <td>May 22, 2026</td>
        <td>May 28, 2026</td>
        <td>Thursday</td>
        <td>12:00 p.m.</td>
        <td>Memorial Day</td>
      </tr>
    </table>
  </body>
</html>
"""


class ScheduleRunnerBehaviorTest(unittest.TestCase):
    def _assert_module_parses_schedule(self, module) -> None:
        schedule = module.parse_schedule_html(SAMPLE_HTML)
        self.assertEqual(schedule.default_release_day_name, "Wednesday")
        self.assertEqual(schedule.default_release_time_eastern, "10:30 a.m.")
        self.assertEqual(len(schedule.holiday_exceptions), 2)
        self.assertTrue(
            any(item.release_date.isoformat() == "2026-05-28" for item in schedule.holiday_exceptions)
        )

        holiday_decision = module.release_decision_for_day(schedule, module.date(2026, 5, 28))
        self.assertIsNotNone(holiday_decision)
        self.assertEqual(holiday_decision.source, "holiday exception")
        self.assertEqual(holiday_decision.release_time_eastern, "12:00 p.m.")

        default_decision = module.release_decision_for_day(schedule, module.date(2026, 6, 3))
        self.assertIsNotNone(default_decision)
        self.assertEqual(default_decision.source, "default Wednesday schedule")
        self.assertEqual(default_decision.release_time_eastern, "10:30 a.m.")

    def test_parse_schedule_html_old_runner(self) -> None:
        self._assert_module_parses_schedule(old_runner)

    def test_parse_schedule_html_new_runner(self) -> None:
        self._assert_module_parses_schedule(new_runner)

    def _assert_missing_cache_forces_live_refresh(self, module) -> None:
        live_schedule = module.parse_schedule_html(SAMPLE_HTML)
        seed_schedule = module.parse_schedule_html(SAMPLE_HTML)
        seed_schedule_json = module.schedule_to_json(seed_schedule)
        seed_schedule_json["fetched_at"] = "2099-01-01T00:00:00-05:00"

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            cache_path = tmp_path / "cache.json"
            seed_path = tmp_path / "seed.json"
            seed_path.write_text(json.dumps(seed_schedule_json) + "\n", encoding="utf-8")

            now_et = module.datetime(2026, 6, 7, 12, 0, tzinfo=module.EASTERN)
            with mock.patch.object(module, "fetch_schedule", return_value=live_schedule) as mocked_fetch:
                resolved = module.resolve_schedule(
                    cache_path=cache_path,
                    seed_path=seed_path,
                    timeout=20.0,
                    refresh_days=7,
                    now_et=now_et,
                )

            mocked_fetch.assert_called_once()
            self.assertEqual(resolved.default_release_time_eastern, "10:30 a.m.")

    def test_missing_cache_forces_live_refresh_old_runner(self) -> None:
        self._assert_missing_cache_forces_live_refresh(old_runner)

    def test_missing_cache_forces_live_refresh_new_runner(self) -> None:
        self._assert_missing_cache_forces_live_refresh(new_runner)

    def _assert_build_command_forwards_output_switches(self, module) -> None:
        args = mock.Mock(
            stats_script=Path("eia_stats.py"),
            interval=0.25,
            duration=120.0,
            timeout=2.5,
            output=Path("eia_stats.png"),
            status_file=Path("eia_stats_status.json"),
            no_clipboard=True,
            no_preview=True,
            force=True,
        )
        command = module.build_stats_command(args)
        self.assertIn("--no-clipboard", command)
        self.assertIn("--no-preview", command)
        self.assertIn("--force", command)

    def test_build_command_forwards_output_switches_old_runner(self) -> None:
        self._assert_build_command_forwards_output_switches(old_runner)

    def test_build_command_forwards_output_switches_new_runner(self) -> None:
        self._assert_build_command_forwards_output_switches(new_runner)


if __name__ == "__main__":
    unittest.main()
