"""Offline checks: atomic rating+alert, set-aside of failing batches, alert drain, llm network errors."""
import json
from pathlib import Path
import tempfile
import unittest
from datetime import timedelta
from unittest import mock
import urllib.error

import alerts
import collection as c
import llm


RATING = {'crypto': 5, 'quality': 4, 'category': 'news', 'reason': 'Specific information'}


def post(ident, age=1):
    return {'id': ident, 'author': 'test', 'text': 'Bitcoin ETF approved ' + ident,
            'published_at': (c.utcnow() - timedelta(hours=age)).isoformat(),
            'url': 'https://x.com/test/status/' + ident, 'collected_at': c.utcnow().isoformat()}


class ReliabilityTests(unittest.TestCase):
    def test_rating_and_alert_commit_together(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            s = c.Store(Path(folder) / 't.db'); q = alerts.AlertQueue(s.db)
            p = post('1'); s.add(p, 'bitcoin', 'pending')
            s.finish('1', RATING, True, q, p)
            self.assertEqual(s.db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0], 1)
            self.assertEqual(s.pending(), [])

    def test_failed_alert_rolls_back_rating(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            s = c.Store(Path(folder) / 't.db'); q = alerts.AlertQueue(s.db)
            p = post('1'); s.add(p, 'bitcoin', 'pending')
            with mock.patch.object(q, 'enqueue', side_effect=RuntimeError('crash')):
                with self.assertRaises(RuntimeError):
                    s.finish('1', RATING, True, q, p)
            s.db.rollback()
            self.assertEqual(len(s.pending()), 1)  # still pending, not silently lost

    def test_empty_text_post_is_rated_once_text_appears(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            s = c.Store(Path(folder) / 't.db')
            p = post('1'); empty = {**p, 'text': ''}
            self.assertTrue(s.add(empty, 'b', 'empty_text'))
            self.assertEqual(s.pending(), [])
            self.assertTrue(s.add(p, 'b', 'pending'))
            self.assertEqual(len(s.pending()), 1)
            s.finish('1', RATING, True)
            self.assertFalse(s.add({**p, 'text': 'changed'}, 'b', 'pending'))  # rated posts are never overwritten
            self.assertEqual(s.pending(), [])

    def test_failing_posts_are_set_aside(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            s = c.Store(Path(folder) / 't.db')
            s.add(post('bad'), 'bitcoin', 'pending'); s.add(post('good'), 'bitcoin', 'pending')
            for _ in range(c.MAX_ATTEMPTS):
                s.fail(['bad'])
            self.assertEqual([p['id'] for p, _ in s.pending()], ['good'])

    def test_expired_alerts_do_not_block_sending(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            s = c.Store(Path(folder) / 't.db'); q = alerts.AlertQueue(s.db)
            for i in range(4):
                p = post(str(i), age=30); s.add(p, 'b', 'pending'); q.enqueue(p, RATING)
            fresh = post('fresh'); s.add(fresh, 'b', 'pending'); q.enqueue(fresh, RATING)
            sent = []

            class Resp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return b''
            with mock.patch.dict('os.environ', {'DISCORD_WEBHOOK_URL': 'https://discord.com/api/webhooks/123/abc'}), \
                 mock.patch('urllib.request.urlopen', lambda req, timeout: sent.append(req) or Resp()):
                q.deliver(Path(folder) / 'preview.jsonl', send=True)
            self.assertEqual(len(sent), 1)

    def test_llm_network_error_ends_in_noanswer(self):
        with mock.patch.dict('os.environ', {'OPENROUTER_API_KEY': 'k'}), \
             mock.patch.object(llm, '_post', side_effect=urllib.error.URLError('down')), \
             mock.patch('time.sleep'):
            with self.assertRaises(llm.NoAnswer):
                llm.ask('m:free', 's', 'u')

    def test_llm_empty_choices_and_bad_retry_after(self):
        with mock.patch.dict('os.environ', {'OPENROUTER_API_KEY': 'k'}), \
             mock.patch.object(llm, '_post', return_value={'choices': []}):
            with self.assertRaises(llm.NoAnswer):
                llm.ask('m:free', 's', 'u')
        err = urllib.error.HTTPError('u', 429, 'x', {'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT'}, None)
        with mock.patch.dict('os.environ', {'OPENROUTER_API_KEY': 'k'}), \
             mock.patch.object(llm, '_post', side_effect=err), mock.patch('time.sleep'):
            with self.assertRaises(llm.NoAnswer):
                llm.ask('m:free', 's', 'u')


if __name__ == '__main__':
    unittest.main()
