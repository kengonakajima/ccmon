#!/usr/bin/env python3
"""Antigravity (agy) のフック (hooks.json) から呼ばれ、セッションの「入力待ち/作業中」状態を
~/.ccmon/state/agy_<conversation_id>.json に書き出す。ccmon.py がこのディレクトリを監視する。

標準入力に agy のフック入力 (camelCase JSON) が渡される。
標準出力にはフックの応答 JSON を必ず出す。agy の PreToolUse は `decision` が必須で、
`{}` だと「hook による拒否」としてツールが実行されない。そこで PreToolUse には
`{"decision": "ask"}`（通常の許可確認に回す。accept-edits や「常に許可」はそのまま効く）、
それ以外のイベントには `{}` を返し、ccmon は agy の権限判定に干渉しない。
"""
import json
import os
import sys
import time
from pathlib import Path

# テスト時は CCMON_STATE_DIR で別ディレクトリに書ける（本番の ccmon を鳴らさないため）
STATE_DIR = Path(os.environ.get('CCMON_STATE_DIR') or (Path.home() / '.ccmon' / 'state'))
CCMON_DIR = Path.home() / '.ccmon'

# ユーザーの許可待ち・入力待ちになり得るツール
WAITING_TOOLS = {
    'run_command',
    'edit_file',
    'write_file',
    'write_to_file',
    'replace_file_content',
    'multi_replace_file_content',
    'ask_question',
}


def classify(data: dict):
    """フック入力から状態を返す: ('waiting'|'background'|'working'|None, 判定理由)"""
    event = data.get('hookEventName')
    if event == 'PreToolUse':
        tool_call = data.get('toolCall') or {}
        tool_name = str(tool_call.get('name') or '')
        if tool_name in WAITING_TOOLS:
            return 'waiting', f'agy:{tool_name}'
        return 'working', f'agy:PreToolUse:{tool_name}'

    if event in ('PostToolUse', 'PreInvocation', 'SessionStart'):
        return 'working', f'agy:{event}'

    if event == 'Stop':
        reason = str(data.get('terminationReason') or 'STOP')
        if reason == 'USER_CANCELED':
            return 'working', 'agy:USER_CANCELED'
        if data.get('fullyIdle') is False:
            return 'background', 'agy:background_tasks'
        return 'waiting', f'agy:Stop:{reason}'

    return None, None


def hook_response(data) -> dict:
    """agy に返す応答。PreToolUse だけは decision が必須なので、通常の許可確認に回す。"""
    if isinstance(data, dict) and data.get('hookEventName') == 'PreToolUse':
        return {'decision': 'ask'}
    return {}


def record_state(data: dict):
    # ~/.ccmon/hook_debug が存在すれば生の入力を記録する（判定の調査用）
    try:
        if (CCMON_DIR / 'hook_debug').exists():
            with open(CCMON_DIR / 'hook_agy_raw.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps(data, ensure_ascii=False) + '\n')
    except OSError:
        pass

    conv_id = data.get('conversationId')
    if not conv_id:
        return
    safe_id = ''.join(c for c in str(conv_id) if c.isalnum() or c in '-_')
    if not safe_id:
        return

    state, reason = classify(data)
    if state is None:
        return

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f'agy_{safe_id}.json'
    cwd = data.get('cwd')
    if not cwd:
        workspace_paths = data.get('workspacePaths') or []
        if workspace_paths:
            cwd = workspace_paths[0]
    record = {
        'session_id': f'agy:{conv_id}',
        'state': state,
        'reason': reason,
        'event': data.get('hookEventName'),
        'cwd': cwd,
        'ts': time.time(),
    }
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False)
    os.replace(tmp, path)


def main():
    data = None
    try:
        data = json.load(sys.stdin)
        if isinstance(data, dict):
            record_state(data)
    except Exception:
        # 状態の記録に失敗しても agy の動作は妨げない
        pass
    finally:
        print(json.dumps(hook_response(data)))


if __name__ == '__main__':
    main()
