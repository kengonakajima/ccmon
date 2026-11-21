import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from watchdog.events import FileCreatedEvent, FileModifiedEvent

import ccmon
from ccmon import (
    ActivityTracker,
    AgySessionsHandler,
    build_ui,
    detect_agy_waiting,
    is_agy_transcript_file,
)


def agy_record(step_index, kind, status='DONE', source='MODEL', **fields):
    record = {
        'step_index': step_index,
        'source': source,
        'type': kind,
        'status': status,
        'created_at': f'2026-11-21T00:00:{step_index:02d}Z',
        **fields,
    }
    return record


class AgyNotificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.sound = Mock()
        self.tracker = ActivityTracker()
        self.handler = AgySessionsHandler(self.sound, self.tracker)

    def transcript(self, conv_id, *records):
        path = self.root / conv_id / '.system_generated' / 'logs' / 'transcript.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        self.append(path, *records)
        return path

    def append(self, path, *records):
        with path.open('a', encoding='utf-8') as stream:
            for record in records:
                stream.write(json.dumps(record) + '\n')

    def created(self, path):
        self.handler.on_created(FileCreatedEvent(str(path)))

    def modified(self, path):
        self.handler.on_modified(FileModifiedEvent(str(path)))

    def test_is_agy_transcript_file_filters_non_transcript_files(self):
        self.assertTrue(is_agy_transcript_file(
            '/Users/test/.gemini/antigravity-cli/brain/conv-1/.system_generated/logs/transcript.jsonl'))
        self.assertFalse(is_agy_transcript_file(
            '/Users/test/.gemini/antigravity-cli/brain/conv-1/.system_generated/logs/transcript_full.jsonl'))
        self.assertFalse(is_agy_transcript_file(
            '/Users/test/.gemini/antigravity-cli/brain/conv-1/scratch/transcript.jsonl'))

    def test_activity_beeps_and_ignores_full_transcript(self):
        path = self.transcript(
            'conv-1',
            agy_record(0, 'USER_INPUT', source='USER_EXPLICIT', content='hello'),
        )
        full_path = path.with_name('transcript_full.jsonl')
        full_path.write_text(json.dumps(agy_record(0, 'USER_INPUT', source='USER_EXPLICIT', content='hello')) + '\n')

        self.created(full_path)
        self.sound.play_beeps.assert_not_called()
        self.assertFalse(self.tracker.is_active('agy'))

        self.created(path)
        self.sound.play_beeps.assert_called_once()
        self.sound.play_neko.assert_not_called()
        self.assertTrue(self.tracker.is_active('agy'))

    def test_tool_call_and_tool_result_are_not_waiting(self):
        path = self.transcript(
            'conv-1',
            agy_record(0, 'USER_INPUT', source='USER_EXPLICIT', content='check'),
            agy_record(1, 'PLANNER_RESPONSE', tool_calls=[{'name': 'run_command', 'args': {}}]),
        )
        self.assertIsNone(detect_agy_waiting(str(path)))

        self.append(path, agy_record(2, 'GENERIC', status='RUNNING', content='running'))
        self.assertIsNone(detect_agy_waiting(str(path)))

        self.append(path, agy_record(3, 'GENERIC', status='DONE', content='done'))
        self.assertIsNone(detect_agy_waiting(str(path)))

        self.append(path, agy_record(4, 'SYSTEM_MESSAGE', source='SYSTEM', content='note'))
        self.assertIsNone(detect_agy_waiting(str(path)))

    def test_turn_completion_and_ask_question_trigger_neko(self):
        path = self.transcript(
            'conv-1',
            agy_record(0, 'USER_INPUT', source='USER_EXPLICIT', content='help'),
            agy_record(1, 'PLANNER_RESPONSE', tool_calls=[{'name': 'ask_question', 'args': {}}]),
        )
        self.assertEqual(detect_agy_waiting(str(path)), 'ask:1')
        self.modified(path)
        self.modified(path)
        self.sound.stop.assert_called_once()
        self.sound.play_neko.assert_called_once()

        # ユーザーが回答し、最終的にテキスト応答でターン完了する
        self.append(path, agy_record(2, 'GENERIC', content='answer'))
        self.modified(path)
        self.append(path, agy_record(3, 'PLANNER_RESPONSE', content='完了しました。'))
        self.assertEqual(detect_agy_waiting(str(path)), 'end:3')
        self.modified(path)
        self.modified(path)
        self.assertEqual(self.sound.stop.call_count, 2)
        self.assertEqual(self.sound.play_neko.call_count, 2)

    def test_interleaved_sessions_notify_once_per_step(self):
        first = self.transcript('conv-1', agy_record(1, 'PLANNER_RESPONSE', content='done 1'))
        second = self.transcript('conv-2', agy_record(1, 'PLANNER_RESPONSE', content='done 2'))
        for path in (first, second, first, second):
            self.modified(path)
        self.assertEqual(self.sound.play_neko.call_count, 2)

    def test_incomplete_or_non_done_planner_response_is_not_waiting(self):
        cases = (
            agy_record(1, 'PLANNER_RESPONSE', status='RUNNING', content='partial'),
            agy_record(2, 'PLANNER_RESPONSE', status='ERROR', content='failed'),
            agy_record(3, 'PLANNER_RESPONSE', status='DONE', content='   '),
            agy_record(4, 'PLANNER_RESPONSE', status='DONE'),
        )
        for idx, rec in enumerate(cases):
            with self.subTest(rec=rec):
                path = self.transcript(f'case-{idx}', rec)
                self.assertIsNone(detect_agy_waiting(str(path)))

        partial = self.transcript('partial', agy_record(1, 'PLANNER_RESPONSE', content='done'))
        with partial.open('a', encoding='utf-8') as stream:
            stream.write('{"step_index":2,"type":"PLANNER_RESPONSE"')
        self.assertIsNone(detect_agy_waiting(str(partial)))

        fallback = self.transcript('fallback', {
            'source': 'MODEL',
            'type': 'PLANNER_RESPONSE',
            'status': 'DONE',
            'created_at': '2026-11-21T00:00:05Z',
            'content': 'done without step_index',
        })
        self.assertEqual(detect_agy_waiting(str(fallback)), 'end:2026-11-21T00:00:05Z')

    def test_ui_includes_agy_badge(self):
        panel = build_ui(True, 2, {'agy': True}, 'macOS既定出力 (afplay)')
        self.assertIn('[Agy]', panel.renderable.plain)

    def test_native_watcher_detects_hidden_agy_transcript(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'CCMON_POLLING': '0'}):
            activity = threading.Event()
            completed = threading.Event()
            sound = Mock()
            sound.play_beeps.side_effect = activity.set
            sound.play_neko.side_effect = completed.set
            handler = AgySessionsHandler(sound, ActivityTracker())
            observer = ccmon.create_observer()
            observer.schedule(handler, directory, recursive=True)
            observer.start()
            try:
                path = Path(directory) / 'conv-new' / '.system_generated' / 'logs' / 'transcript.jsonl'
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(agy_record(0, 'USER_INPUT', source='USER_EXPLICIT', content='hi')) + '\n')
                self.assertTrue(activity.wait(5), 'New agy transcript was not detected')
                with path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(agy_record(1, 'PLANNER_RESPONSE', content='done')) + '\n')
                self.assertTrue(completed.wait(5), 'Appended agy completion was not detected')
            finally:
                observer.stop()
                observer.join(5)
            sound.play_neko.assert_called_once()

    def test_main_starts_with_only_agy_directory(self):
        with tempfile.TemporaryDirectory() as home:
            cli_brain = Path(home) / '.gemini' / 'antigravity-cli' / 'brain'
            app_brain = Path(home) / '.gemini' / 'antigravity' / 'brain'
            cli_brain.mkdir(parents=True)
            app_brain.mkdir(parents=True)
            observer = Mock()
            with patch('ccmon.Path.home', return_value=Path(home)), \
                 patch('ccmon.load_sound_enabled', return_value=True), \
                 patch('ccmon.load_volume_level', return_value=2), \
                 patch('ccmon.create_observer', return_value=observer), \
                 patch('ccmon.run_tui', side_effect=KeyboardInterrupt), \
                 patch.object(sys.stdin, 'isatty', return_value=True), \
                 patch.object(sys.stdout, 'isatty', return_value=True):
                ccmon.main()
            scheduled_paths = [call.args[1] for call in observer.schedule.call_args_list]
            self.assertEqual(scheduled_paths, [str(cli_brain), str(app_brain)])
            observer.start.assert_called_once()


if __name__ == '__main__':
    unittest.main()
