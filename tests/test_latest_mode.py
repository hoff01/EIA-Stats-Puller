import importlib
import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch


class LatestModeTests(unittest.TestCase):
    def test_latest_poll_retries_then_republishes_existing_week(self):
        for variant in ("old_stats", "new_stats"):
            module = importlib.import_module(f"{variant}.eia_stats")
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                with patch("sys.argv", ["stats", "--poll", "--latest", "--status-file", str(Path(directory) / "status.json")]):
                    args = module.parse_args()
                now = [0.0]
                def sleep(seconds):
                    now[0] += seconds
                fetched = module.FetchResult(data={}, elapsed_seconds=0.01)
                with patch.object(module, "load_status", return_value={"generated": [{"date": "2026-09-18"}]}), \
                     patch.object(module, "target_friday", side_effect=AssertionError("calendar target used")), \
                     patch.object(module, "make_http_client"), \
                     patch.object(module, "fetch_release_probe", side_effect=[ValueError("blank"), (date(2026, 9, 18), fetched)]) as probe, \
                     patch.object(module, "create_output", return_value=(True, "Ready")) as create, \
                     patch.object(module.time, "monotonic", side_effect=lambda: now[0]), \
                     patch.object(module.time, "sleep", side_effect=sleep), redirect_stdout(io.StringIO()):
                    self.assertEqual(module.poll(args), 0)
                self.assertEqual(probe.call_count, 2)
                self.assertEqual(now[0], 0.5)
                self.assertTrue(create.call_args.kwargs["force"])
                self.assertFalse(create.call_args.kwargs["require_target"])
                self.assertFalse(create.call_args.kwargs["no_clipboard"])
                self.assertEqual(create.call_args.kwargs["target"], date(2026, 9, 18))

    def test_latest_poll_stops_after_two_minutes_of_unavailability(self):
        for variant in ("old_stats", "new_stats"):
            module = importlib.import_module(f"{variant}.eia_stats")
            with patch("sys.argv", ["stats", "--poll", "--latest"]):
                args = module.parse_args()
            now = [0.0]
            def sleep(seconds):
                now[0] += seconds
            with patch.object(module, "make_http_client"), \
                 patch.object(module, "fetch_release_probe", side_effect=ValueError("unavailable")) as probe, \
                 patch.object(module.time, "monotonic", side_effect=lambda: now[0]), \
                 patch.object(module.time, "sleep", side_effect=sleep), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(module.poll(args), 2)
            self.assertEqual(probe.call_count, 240)
            self.assertEqual(now[0], 120)

    def test_latest_cannot_also_require_future_target(self):
        for variant in ("old_stats", "new_stats"):
            module = importlib.import_module(f"{variant}.eia_stats")
            with patch("sys.argv", ["stats", "--latest", "--target-date", "2026-09-25"]), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    module.parse_args()

    def test_runner_non_release_day_uses_latest_and_release_day_uses_explicit_week(self):
        for variant in ("old_stats", "new_stats"):
            module = importlib.import_module(f"{variant}.wpsr_schedule_runner")
            holiday = module.HolidayException(date(2026, 9, 4), date(2026, 9, 9), date(2026, 9, 10), "Thursday", "11:00 a.m.", "Labor Day")
            schedule = module.ScheduleData("test", datetime(2026, 9, 1, tzinfo=module.EASTERN), "Wednesday", 2, "10:30 a.m.", [holiday])
            for now, expected in (("2026-09-25T12:00:00", {"latest": True}),
                                  ("2026-09-23T10:28:00", {"target_date": date(2026, 9, 18)}),
                                  ("2026-09-10T10:00:00", {"target_date": date(2026, 9, 4)})):
                with patch("sys.argv", ["runner", "--now-eastern", now]), \
                     patch.object(module, "resolve_schedule", return_value=schedule), \
                     patch.object(module, "save_schedule_file"), \
                     patch.object(module, "wait_until_release") as wait, \
                     patch.object(module, "run_stats_command", return_value=0) as run, redirect_stdout(io.StringIO()):
                    self.assertEqual(module.main(), 0)
                self.assertEqual(run.call_args.kwargs, expected)
                self.assertEqual(wait.call_count, 0 if "latest" in expected else 1)

    def test_latest_override_avoids_schedule_and_show_decision_does_not_publish(self):
        for variant in ("old_stats", "new_stats"):
            module = importlib.import_module(f"{variant}.wpsr_schedule_runner")
            for show in (False, True):
                argv = ["runner", "--latest"] + (["--show-decision"] if show else [])
                with patch("sys.argv", argv), \
                     patch.object(module, "resolve_schedule", side_effect=AssertionError("calendar used")), \
                     patch.object(module, "run_stats_command", return_value=0) as run, redirect_stdout(io.StringIO()):
                    self.assertEqual(module.main(), 0)
                self.assertEqual(run.call_count, 0 if show else 1)
                if not show:
                    self.assertEqual(run.call_args.kwargs, {"latest": True})

    def test_schedule_failure_falls_back_to_live_latest(self):
        for variant in ("old_stats", "new_stats"):
            module = importlib.import_module(f"{variant}.wpsr_schedule_runner")
            with patch("sys.argv", ["runner"]), \
                 patch.object(module, "resolve_schedule", side_effect=OSError("unavailable")), \
                 patch.object(module, "run_stats_command", return_value=0) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(), 0)
            self.assertEqual(run.call_args.kwargs, {"latest": True})

    def test_latest_and_target_are_forwarded_to_polling_process(self):
        for variant in ("old_stats", "new_stats"):
            module = importlib.import_module(f"{variant}.wpsr_schedule_runner")
            with patch("sys.argv", ["runner"]):
                args = module.parse_args()
            command = module.build_stats_command(args, latest=True)
            self.assertIn("--poll", command)
            self.assertIn("--latest", command)
            self.assertNotIn("--no-clipboard", command)
            command = module.build_stats_command(args, target_date=date(2026, 9, 18))
            self.assertEqual(command[command.index("--target-date") + 1], "2026-09-18")


if __name__ == "__main__":
    unittest.main()
