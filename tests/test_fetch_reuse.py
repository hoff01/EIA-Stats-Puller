import importlib
import unittest
from collections import Counter
from threading import Lock
from unittest.mock import Mock, patch

import httpx

from old_stats import eia_stats as stats


class FetchReuseTests(unittest.TestCase):
    def test_reuses_probe_client_and_retries_only_failed_table(self):
        counts = Counter()
        lock = Lock()
        content = b'STUB_1,9/11/2026,9/4/2026\nTotal,10,9\n'

        def respond(request):
            name = request.url.path.rsplit('/', 1)[-1]
            with lock:
                counts[name] += 1
                attempt = counts[name]
            return httpx.Response(503 if name == 'table6.csv' and attempt == 1 else 200, content=content)

        with httpx.Client(transport=httpx.MockTransport(respond)) as client, \
             patch.object(stats, 'make_http_client') as create, \
             patch.object(stats.time, 'sleep'):
            initial = stats.fetch_csv_table('table4', 2.5, client)
            result = stats.fetch_wpsr(2.5, initial_tables={'table4': initial}, client=client)
            create.assert_not_called()
            self.assertFalse(client.is_closed)
            self.assertIs(result.data['table4'], initial)
        self.assertEqual(counts, {'table4.csv': 1, 'table5a.csv': 1, 'table6.csv': 2})

    def test_owned_client_closed_when_retries_exhausted(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
        with patch.object(stats, 'make_http_client', return_value=client) as create, \
             patch.object(stats.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'Could not fetch'):
                stats.fetch_wpsr(2.5)
        create.assert_called_once()
        self.assertTrue(client.is_closed)

    def test_mixed_weeks_and_blank_pages_remain_rejected(self):
        latest = stats.parse_csv_table(b'STUB_1,9/11/2026,9/4/2026\nTotal,10,9\n', 'table4')
        stale = stats.parse_csv_table(b'STUB_1,9/4/2026,8/28/2026\nTotal,10,9\n', 'table6')
        with self.assertRaisesRegex(ValueError, 'Mismatched release dates'):
            stats.build_stats({'table4': latest, 'table5a': latest, 'table6': stale})
        for content in (b'', b'<html>Not ready</html>'):
            with self.assertRaises(ValueError):
                stats.parse_csv_table(content, 'table4')

    def test_json_retries_share_one_client(self):
        module = importlib.import_module('new_stats.eia_stats')
        client = Mock()
        with patch.object(module, 'make_http_client') as create, \
             patch.object(module, 'fetch_wpsr_json', side_effect=[ValueError('blank'), {'series': []}]) as fetch, \
             patch.object(module.time, 'sleep'):
            create.return_value.__enter__.return_value = client
            module.fetch_wpsr(2.5)
            create.assert_called_once()
            self.assertTrue(all(call.kwargs['client'] is client for call in fetch.call_args_list))

    def test_poll_defaults_remain_bounded(self):
        for variant in ('old_stats', 'new_stats'):
            module = importlib.import_module(f'{variant}.eia_stats')
            with patch('sys.argv', ['eia_stats.py', '--poll']), patch.dict(module.os.environ, {}, clear=True):
                args = module.parse_args()
            self.assertEqual((args.interval, args.duration), (0.5, 120.0))
