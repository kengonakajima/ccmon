import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from watchdog.events import FileModifiedEvent

from ccmon import CodexSessionsHandler, detect_claude_waiting, detect_codex_waiting


def event(kind, turn_id='turn-1', **fields):
    return {'type': 'event_msg', 'payload': {
        'type': kind, 'turn_id': turn_id, **fields,
    }}


class CodexNotificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.sound = Mock()
        self.handler = CodexSessionsHandler(self.sound, Mock())

    def session(self, name, source, *records):
        path = self.root / (name + '.jsonl')
        self.append(path, {'type': 'session_meta', 'payload': {'source': source}},
                    *records)
        return path

    def append(self, path, *records):
        with path.open('a', encoding='utf-8') as stream:
            for record in records:
                stream.write(json.dumps(record) + '\n')

    def modified(self, path):
        self.handler.on_modified(FileModifiedEvent(str(path)))

    def test_main_session_completion(self):
        for source in ('cli', 'vscode', 'exec'):
            with self.subTest(source=source):
                path = self.session(source, source, event('task_complete'))
                self.assertEqual(detect_codex_waiting(str(path)), 'task:turn-1')

    def test_internal_agent_completion_does_not_interrupt_parent(self):
        parent = self.session('parent', 'vscode', event('task_started'))
        self.modified(parent)
        self.sound.play_beeps.assert_called_once()

        # 実ログのguardianに加え、他のsubagent表現でも通知しない。
        sources = ({'subagent': {'other': 'guardian'}},
                   {'subagent': {'thread_spawn': {'parent_thread_id': 'parent'}}},
                   {'subagent': 'review'}, {'subagent': 'compact'}, 'subagent')
        for index, source in enumerate(sources):
            with self.subTest(source=source):
                child = self.session(str(index), source, event('task_complete'))
                self.modified(child)
                self.sound.play_neko.assert_not_called()
                self.sound.stop.assert_not_called()

        self.append(parent, event('task_complete'))
        self.modified(parent)
        self.sound.play_neko.assert_called_once()
        self.sound.stop.assert_called_once()

    def test_work_in_progress_is_not_completion(self):
        for kind in ('task_started', 'item_completed', 'agent_message',
                     'token_count', 'turn_aborted'):
            with self.subTest(kind=kind):
                path = self.session(kind, 'vscode', event('task_complete'), event(kind))
                self.assertIsNone(detect_codex_waiting(str(path)))

    def test_interleaved_sessions_notify_once_per_turn(self):
        # 同じturn_idでも別セッションは別の完了として扱う。
        first = self.session('first', 'vscode', event('task_complete'))
        second = self.session('second', 'vscode', event('task_complete'))
        for path in (first, second, first, second):
            self.modified(path)
        self.assertEqual(self.sound.play_neko.call_count, 2)
        self.append(first, event('task_started', 'turn-2'))
        self.modified(first)
        self.append(first, event('task_complete', 'turn-2'))
        self.modified(first)
        self.modified(first)
        self.assertEqual(self.sound.play_neko.call_count, 3)

    def test_unreadable_or_incomplete_metadata_is_not_completion(self):
        records = (None, [], {}, {'type': 'session_meta', 'payload': []},
                   {'type': 'session_meta', 'payload': {}},
                   {'type': 'session_meta', 'payload': {'source': None}},
                   {'type': 'session_meta', 'payload': {'source': ''}})
        for index, metadata in enumerate(records):
            with self.subTest(metadata=metadata):
                path = self.root / (str(index) + '.jsonl')
                self.append(path, metadata, event('task_complete'))
                self.assertIsNone(detect_codex_waiting(str(path)))
        path = self.root / 'partial.jsonl'
        path.write_text('{\n' + json.dumps(event('task_complete')) + '\n')
        self.assertIsNone(detect_codex_waiting(str(path)))
        self.assertIsNone(detect_codex_waiting(str(self.root / 'missing.jsonl')))

    def test_invalid_completion_is_not_notified(self):
        records = (event('task_complete', None), event('task_complete', ''),
                   {'type': 'event_msg', 'payload': []},
                   {'type': 'event_msg', 'payload': 'task_complete'})
        for index, record in enumerate(records):
            with self.subTest(record=record):
                path = self.session(str(index), 'vscode', record)
                self.assertIsNone(detect_codex_waiting(str(path)))

    def test_partial_tail_does_not_reuse_previous_completion(self):
        path = self.session('partial', 'vscode', event('task_complete'))
        with path.open('a') as stream:
            stream.write('{"type":"event_msg","payload":')
        self.assertIsNone(detect_codex_waiting(str(path)))

    def test_claude_completion_and_question_still_notify(self):
        path = self.root / 'claude.jsonl'
        self.append(path, {'type': 'assistant', 'message': {
            'id': 'message-1', 'stop_reason': 'end_turn',
        }})
        self.assertEqual(detect_claude_waiting(str(path)), 'end:message-1')
        self.append(path, {'type': 'assistant', 'message': {'content': [{
            'type': 'tool_use', 'name': 'AskUserQuestion', 'id': 'question-1',
        }]}})
        self.assertEqual(detect_claude_waiting(str(path)), 'ask:question-1')


if __name__ == '__main__':
    unittest.main()
