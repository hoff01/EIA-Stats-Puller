import importlib
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class PollDeliveryTests(unittest.TestCase):
    def args(self, root):
        return SimpleNamespace(duration=120.0, interval=0.5, timeout=2.5, force=False, latest=False,
                               target_date=date(2026, 9, 18), status_file=root / 'status.json',
                               output=root / 'stats.png', no_clipboard=False, no_preview=False)

    def test_slow_request_finishes_without_starting_another_after_deadline(self):
        for variant in ('old_stats', 'new_stats'):
            module = importlib.import_module(f'{variant}.eia_stats')
            clock = Clock()

            def slow(*args):
                clock.now += 2.5
                raise httpx.ReadTimeout('Slow response')

            with tempfile.TemporaryDirectory() as directory, \
                 patch.object(module, 'make_http_client'), \
                 patch.object(module, 'fetch_release_probe', side_effect=slow) as probe, \
                 patch.object(module.time, 'monotonic', side_effect=lambda: clock.now), \
                 patch.object(module.time, 'sleep', side_effect=clock.sleep), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                args = self.args(Path(directory))
                args.duration = 2.0
                self.assertEqual(module.poll(args), 2)
            probe.assert_called_once()
            self.assertEqual(clock.sleeps, [])

    def test_schedule_and_powershell_defaults_agree(self):
        root = Path(__file__).resolve().parents[1]
        for variant in ('old_stats', 'new_stats'):
            module = importlib.import_module(f'{variant}.wpsr_schedule_runner')
            with patch('sys.argv', ['wpsr_schedule_runner.py']):
                args = module.parse_args()
            self.assertEqual((args.interval, args.duration), (0.5, 120.0))
            script = (root / variant / 'run_eia_stats_task.ps1').read_text()
            self.assertIn('[double]$IntervalSeconds = 0.5,', script)
            self.assertIn('[double]$DurationSeconds = 120,', script)

    def test_unavailable_page_retries_half_second_until_two_minutes(self):
        for variant in ('old_stats', 'new_stats'):
            module = importlib.import_module(f'{variant}.eia_stats')
            clock = Clock()
            starts = []

            def unavailable(*args):
                starts.append(clock.now)
                raise httpx.ConnectError('Temporarily unavailable')

            with tempfile.TemporaryDirectory() as directory, \
                 patch.object(module, 'make_http_client'), \
                 patch.object(module, 'fetch_release_probe', side_effect=unavailable), \
                 patch.object(module, 'create_output') as create, \
                 patch.object(module.time, 'monotonic', side_effect=lambda: clock.now), \
                 patch.object(module.time, 'sleep', side_effect=clock.sleep), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(module.poll(self.args(Path(directory))), 2)
            self.assertEqual(starts, [i * 0.5 for i in range(240)])
            self.assertEqual(clock.now, 120.0)
            create.assert_not_called()

    def test_blank_http_error_stale_then_ready_only_publishes_once(self):
        for variant in ('old_stats', 'new_stats'):
            module = importlib.import_module(f'{variant}.eia_stats')
            clock = Clock()
            expected = date(2026, 9, 18)
            fetched = module.FetchResult(data={}, elapsed_seconds=0.01)
            response = httpx.Response(503, request=httpx.Request('GET', 'https://example.com'))
            failures = [ValueError('blank page'),
                        httpx.HTTPStatusError('Unavailable', request=response.request, response=response),
                        (expected - timedelta(days=7), fetched), (expected, fetched)]
            with tempfile.TemporaryDirectory() as directory, \
                 patch.object(module, 'make_http_client'), \
                 patch.object(module, 'fetch_release_probe', side_effect=failures) as probe, \
                 patch.object(module, 'create_output', return_value=(True, 'Ready')) as create, \
                 patch.object(module.time, 'monotonic', side_effect=lambda: clock.now), \
                 patch.object(module.time, 'sleep', side_effect=clock.sleep), redirect_stdout(io.StringIO()):
                self.assertEqual(module.poll(self.args(Path(directory))), 0)
            self.assertEqual(probe.call_count, 4)
            self.assertEqual(clock.sleeps, [0.5, 0.5, 0.5])
            create.assert_called_once()
            self.assertFalse(create.call_args.kwargs['no_clipboard'])

    def test_clipboard_precedes_preview_and_archive_with_retries(self):
        for variant in ('old_stats', 'new_stats'):
            module = importlib.import_module(f'{variant}.eia_stats')
            events = []
            clipboard_attempts = iter([False, True])

            def clipboard(path):
                events.append('clipboard')
                return next(clipboard_attempts)

            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / 'stats.png'
                fetched = module.FetchResult(data={}, elapsed_seconds=0.01)
                with patch.object(module, 'fetch_wpsr', return_value=fetched), \
                     patch.object(module, 'build_stats', return_value=(date(2026, 9, 18), [])), \
                     patch.object(module, 'render_image', return_value=output), \
                     patch.object(module, 'copy_image_to_clipboard', side_effect=clipboard), \
                     patch.object(module, 'open_image_preview', side_effect=lambda path: events.append('preview') or True), \
                     patch.object(module, 'copy2', side_effect=lambda *args: events.append('archive')), \
                     patch.object(module.time, 'sleep') as sleep, patch.dict(module.os.environ, {}, clear=True):
                    generated, message = module.create_output(
                        output_path=output, status_path=root / 'status.json', no_clipboard=False,
                        no_preview=False, force=True, timeout=2.5, require_target=False)
                self.assertTrue(generated)
                self.assertIn('clipboard=True', message)
                self.assertEqual(events, ['clipboard', 'clipboard', 'preview', 'archive'])
                sleep.assert_called_once_with(0.25)
                self.assertTrue(module.load_status(root / 'status.json')['generated'][-1]['clipboard'])
