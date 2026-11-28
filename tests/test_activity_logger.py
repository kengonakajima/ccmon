import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import ccmon


class TestActivityLogger(unittest.TestCase):
    def _make_logger(self, tmpdir, *, idle_fn, frontmost_fn, now_fn, agents=None, waiting=None,
                     interval=10.0, heartbeat=300.0):
        tracker = Mock()
        tracker.snapshot.return_value = dict(agents or {})
        registry = Mock()
        registry.waiting_sessions.return_value = list(waiting or [])
        logger = ccmon.ActivityLogger(
            tracker,
            registry,
            log_dir=Path(tmpdir),
            interval_seconds=interval,
            heartbeat_seconds=heartbeat,
            idle_fn=idle_fn,
            frontmost_fn=frontmost_fn,
            now_fn=now_fn,
        )
        return logger, tracker, registry

    def test_records_active_operation_with_app_and_agents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            t = 1795874400.0  # 2026-11-28
            front_mock = Mock(return_value='Claude')
            logger, _, _ = self._make_logger(
                tmpdir,
                idle_fn=lambda: 1.5,
                frontmost_fn=front_mock,
                now_fn=lambda: t,
                agents={'claude': True, 'codex': False},
                waiting=[{'session_id': 'agy_123', 'cwd': '/Users/me/arkn'}],
            )
            rec = logger.sample_once()
            self.assertIsNotNone(rec)
            self.assertTrue(rec['active'])
            self.assertEqual(rec['idle_s'], 1.5)
            self.assertEqual(rec['last_input_ts'], round(t - 1.5, 1))
            self.assertEqual(rec['app'], 'Claude')
            self.assertEqual(rec['agents'], ['claude'])
            self.assertEqual(rec['waiting'], ['agy:arkn'])
            # Privacy check: no keystroke or window title fields
            self.assertEqual(
                set(rec.keys()),
                {'ts', 'iso', 'active', 'idle_s', 'last_input_ts', 'app', 'agents', 'waiting'},
            )

            files = list(Path(tmpdir).glob('*.jsonl'))
            self.assertEqual(len(files), 1)
            lines = files[0].read_text(encoding='utf-8').strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0]), rec)

    def test_idle_without_agents_skips_redundant_writes_and_app_polling_until_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clock = {'t': 1795874400.0, 'idle': 2.0}
            front_mock = Mock(return_value='Ghostty')
            logger, _, _ = self._make_logger(
                tmpdir,
                idle_fn=lambda: clock['idle'],
                frontmost_fn=front_mock,
                now_fn=lambda: clock['t'],
                agents={'claude': False},
                waiting=[],
                interval=10.0,
                heartbeat=300.0,
            )
            # 1) Active tick -> writes & queries frontmost app
            r1 = logger.sample_once()
            self.assertIsNotNone(r1)
            self.assertTrue(r1['active'])
            self.assertEqual(front_mock.call_count, 1)

            # 2) Transition active -> idle -> writes transition & queries app
            clock['t'] += 10.0
            clock['idle'] = 12.0
            r2 = logger.sample_once()
            self.assertIsNotNone(r2)
            self.assertFalse(r2['active'])
            self.assertEqual(front_mock.call_count, 2)

            # 3) Continued idle (before heartbeat) -> skips write AND skips frontmost app call
            clock['t'] += 10.0
            clock['idle'] = 22.0
            r3 = logger.sample_once()
            self.assertIsNone(r3)
            self.assertEqual(front_mock.call_count, 2)

            # 4) After heartbeat (300s) -> writes heartbeat
            clock['t'] += 300.0
            clock['idle'] = 322.0
            r4 = logger.sample_once()
            self.assertIsNotNone(r4)
            self.assertFalse(r4['active'])
            self.assertEqual(r4['idle_s'], 322.0)

            files = list(Path(tmpdir).glob('*.jsonl'))
            lines = files[0].read_text(encoding='utf-8').strip().splitlines()
            self.assertEqual(len(lines), 3)

    def test_idle_while_agent_active_keeps_logging(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clock = {'t': 1795874400.0, 'idle': 45.0}
            front_mock = Mock(return_value='Claude')
            logger, _, _ = self._make_logger(
                tmpdir,
                idle_fn=lambda: clock['idle'],
                frontmost_fn=front_mock,
                now_fn=lambda: clock['t'],
                agents={'claude': True},
            )
            r1 = logger.sample_once()
            clock['t'] += 10.0
            clock['idle'] = 55.0
            r2 = logger.sample_once()
            self.assertIsNotNone(r1)
            self.assertIsNotNone(r2)
            self.assertFalse(r2['active'])
            self.assertEqual(r2['agents'], ['claude'])

    def test_rotates_log_file_by_month(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jst = timezone(timedelta(hours=9))
            t_nov = datetime(2026, 11, 30, 23, 59, 55, tzinfo=jst).timestamp()
            t_dec = datetime(2026, 12, 1, 0, 0, 5, tzinfo=jst).timestamp()
            clock = {'t': t_nov}
            logger, _, _ = self._make_logger(
                tmpdir,
                idle_fn=lambda: 1.0,
                frontmost_fn=lambda: 'Claude',
                now_fn=lambda: clock['t'],
            )
            logger.sample_once()
            clock['t'] = t_dec
            logger.sample_once()

            names = sorted(p.name for p in Path(tmpdir).glob('*.jsonl'))
            self.assertEqual(names, ['2026-11.jsonl', '2026-12.jsonl'])

    def test_frontmost_app_name_returns_string_without_subprocess(self):
        with patch('ccmon.subprocess.Popen', side_effect=AssertionError('must not spawn subprocess')):
            name = ccmon.frontmost_app_name()
        self.assertTrue(name is None or (isinstance(name, str) and len(name) > 0))


if __name__ == '__main__':
    unittest.main()
