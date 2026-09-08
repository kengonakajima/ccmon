import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import ccmon


class IdleUITests(unittest.TestCase):
    def setUp(self):
        self.console = Mock(size=(80, 24))
        self.sound = Mock(enabled=True, volume_level=2)
        self.sound.get_output_device_label.return_value = 'Output'
        self.tracker = Mock()
        self.tracker.snapshot.return_value = {'codex': False}
        self.input = self.enterContext(patch('ccmon.TerminalInput')).return_value.__enter__.return_value
        self.live_type = self.enterContext(patch('ccmon.Live'))
        self.live = self.live_type.return_value.__enter__.return_value
        self.enterContext(patch('ccmon.time.sleep'))
        self.save_enabled = self.enterContext(patch('ccmon.save_sound_enabled'))
        self.save_volume = self.enterContext(patch('ccmon.save_volume_level'))

    def run_ui(self):
        with self.assertRaises(KeyboardInterrupt):
            ccmon.run_tui(self.console, self.sound, self.tracker)

    def test_idle_does_not_redraw(self):
        self.input.read_keys.side_effect = [[], [], [], ['QUIT']]
        self.run_ui()
        self.live.update.assert_not_called()
        self.assertFalse(self.live_type.call_args.kwargs['auto_refresh'])

    def test_keys_update_display_and_settings(self):
        self.input.read_keys.side_effect = [['RIGHT'], ['LEFT'], ['TOGGLE'], ['QUIT']]
        self.run_ui()
        self.assertEqual(self.live.update.call_count, 3)
        self.assertEqual(self.sound.volume_level, 2)
        self.assertFalse(self.sound.enabled)
        self.sound.stop.assert_called_once()
        self.save_enabled.assert_called_once_with(False)
        self.assertEqual([call.args[0] for call in self.save_volume.call_args_list], [1, 2])

    def test_activity_expiry_and_resize_redraw(self):
        actions = iter(('active', 'idle', 'resize', 'quit'))

        def read_keys():
            action = next(actions)
            if action == 'active':
                self.tracker.snapshot.return_value = {'codex': True}
            elif action == 'idle':
                self.tracker.snapshot.return_value = {'codex': False}
            elif action == 'resize':
                self.console.size = (100, 30)
            else:
                return ['QUIT']
            return []

        self.input.read_keys.side_effect = read_keys
        self.run_ui()
        self.assertEqual(self.live.update.call_count, 3)


class NativeWatcherTests(unittest.TestCase):
    def test_codex_detects_existing_log_append_without_native_events(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict('os.environ', {'CCMON_POLLING': '0'}), \
             patch('ccmon.Observer', side_effect=AssertionError('Native events unavailable')):
            path = Path(directory) / 'session.jsonl'
            path.write_text(json.dumps({
                'type': 'session_meta', 'payload': {'source': 'vscode'},
            }) + '\n')
            completed = threading.Event()
            sound = Mock()
            sound.play_neko.side_effect = completed.set
            observer = ccmon.create_observer(polling=True)
            handler = ccmon.CodexSessionsHandler(sound, ccmon.ActivityTracker())
            observer.schedule(handler, directory, recursive=True)
            observer.start()
            try:
                with path.open('a') as stream:
                    stream.write(json.dumps({'type': 'event_msg', 'payload': {
                        'type': 'task_complete', 'turn_id': 'turn-1',
                    }}) + '\n')
                self.assertTrue(completed.wait(5), 'Completion requires native events')
            finally:
                observer.stop()
                observer.join(5)
            sound.play_neko.assert_called_once()

    def test_new_nested_session_and_completion_are_detected(self):
        # 実際のOS通知で、監視開始後に作られたディレクトリと追記を検知する。
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'CCMON_POLLING': '0'}):
            activity = threading.Event()
            completed = threading.Event()
            sound = Mock()
            sound.play_beeps.side_effect = activity.set
            sound.play_neko.side_effect = completed.set
            handler = ccmon.CodexSessionsHandler(sound, ccmon.ActivityTracker())
            observer = ccmon.create_observer()
            observer.schedule(handler, directory, recursive=True)
            observer.start()
            try:
                path = Path(directory) / 'new' / 'nested' / 'session.jsonl'
                path.parent.mkdir(parents=True)
                metadata = {'type': 'session_meta', 'payload': {'source': 'vscode'}}
                path.write_text(json.dumps(metadata) + '\n')
                self.assertTrue(activity.wait(5), 'New nested session was not detected')
                record = {'type': 'event_msg', 'payload': {
                    'type': 'task_complete', 'turn_id': 'turn-1',
                }}
                with path.open('a') as stream:
                    stream.write(json.dumps(record) + '\n')
                self.assertTrue(completed.wait(5), 'Appended completion was not detected')
            finally:
                observer.stop()
                observer.join(5)
            sound.play_neko.assert_called_once()


if __name__ == '__main__':
    unittest.main()
