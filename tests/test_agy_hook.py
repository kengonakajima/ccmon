import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HOOK_SCRIPT = Path(__file__).resolve().parent.parent / 'hooks' / 'ccmon_agy_state.py'


class TestAgyHook(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def run_hook(self, payload: dict):
        env = os.environ.copy()
        env['CCMON_STATE_DIR'] = str(self.state_dir)
        res = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            check=True,
        )
        expected = {'decision': 'ask'} if payload.get('hookEventName') == 'PreToolUse' else {}
        self.assertEqual(json.loads(res.stdout), expected)
        state_file = self.state_dir / f"agy_{payload['conversationId']}.json"
        if state_file.exists():
            return json.loads(state_file.read_text(encoding='utf-8'))
        return None

    def test_pre_tool_use_waiting_tools(self):
        for tool in ('run_command', 'edit_file', 'write_file', 'ask_question'):
            rec = self.run_hook({
                'conversationId': 'conv-1',
                'hookEventName': 'PreToolUse',
                'cwd': '/Users/test/ccmon',
                'toolCall': {'name': tool, 'args': {}},
            })
            self.assertIsNotNone(rec)
            self.assertEqual(rec['state'], 'waiting')
            self.assertEqual(rec['reason'], f'agy:{tool}')
            self.assertEqual(rec['cwd'], '/Users/test/ccmon')

    def test_pre_tool_use_non_waiting_tool(self):
        rec = self.run_hook({
            'conversationId': 'conv-1',
            'hookEventName': 'PreToolUse',
            'cwd': '/Users/test/ccmon',
            'toolCall': {'name': 'read_file', 'args': {}},
        })
        self.assertEqual(rec['state'], 'working')

    def test_post_tool_use_and_pre_invocation_clear_waiting(self):
        self.run_hook({
            'conversationId': 'conv-2',
            'hookEventName': 'PreToolUse',
            'cwd': '/Users/test/ccmon',
            'toolCall': {'name': 'edit_file', 'args': {}},
        })
        rec = self.run_hook({
            'conversationId': 'conv-2',
            'hookEventName': 'PostToolUse',
            'cwd': '/Users/test/ccmon',
            'toolCall': {'name': 'edit_file', 'args': {}},
        })
        self.assertEqual(rec['state'], 'working')

        rec2 = self.run_hook({
            'conversationId': 'conv-2',
            'hookEventName': 'PreInvocation',
            'cwd': '/Users/test/ccmon',
        })
        self.assertEqual(rec2['state'], 'working')

    def test_stop_normal_waiting(self):
        rec = self.run_hook({
            'conversationId': 'conv-3',
            'hookEventName': 'Stop',
            'terminationReason': 'NO_TOOL_CALL',
            'fullyIdle': True,
            'cwd': '/Users/test/ccmon',
        })
        self.assertEqual(rec['state'], 'waiting')
        self.assertEqual(rec['reason'], 'agy:Stop:NO_TOOL_CALL')

    def test_stop_user_canceled_not_waiting(self):
        rec = self.run_hook({
            'conversationId': 'conv-4',
            'hookEventName': 'Stop',
            'terminationReason': 'USER_CANCELED',
            'fullyIdle': True,
            'cwd': '/Users/test/ccmon',
        })
        self.assertEqual(rec['state'], 'working')

    def test_stop_with_background_tasks_not_waiting(self):
        rec = self.run_hook({
            'conversationId': 'conv-5',
            'hookEventName': 'Stop',
            'terminationReason': 'NO_TOOL_CALL',
            'fullyIdle': False,
            'cwd': '/Users/test/ccmon',
        })
        self.assertEqual(rec['state'], 'background')


if __name__ == '__main__':
    unittest.main()
