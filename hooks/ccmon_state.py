#!/usr/bin/env python3
"""Claude Code のフックから呼ばれ、セッションの「入力待ち/作業中」状態を
~/.ccmon/state/<session_id>.json に書き出す。ccmon.py がこのディレクトリを監視する。

標準入力に Claude Code のフック入力(JSON)が渡される。

Stop（ターン完了）のうち、バックグラウンドタスクが動いているものだけは
「ユーザーがいま何かすべきか」を Claude Haiku に判定させる（1回あたり数百トークン）。
バックグラウンドタスクが無ければ LLM は呼ばず、常に入力待ちとする。
"""
import json
import os
import re
import sys
import time
from pathlib import Path

# テスト時は CCMON_STATE_DIR で別ディレクトリに書ける（本番の ccmon を鳴らさないため）
STATE_DIR = Path(os.environ.get('CCMON_STATE_DIR') or (Path.home() / '.ccmon' / 'state'))
CCMON_DIR = Path.home() / '.ccmon'

# 「入力待ち」とみなす Notification の種類
WAITING_NOTIFICATIONS = {
    'permission_prompt',
    'agent_needs_input',
    'elicitation_dialog',
    'elicitation_url_dialog',
}

FINISHED_TASK_STATUSES = {'completed', 'done', 'failed', 'error', 'cancelled', 'canceled', 'killed', 'stopped'}

# 判定用モデル（安さ優先）。~/keys/claude.md は MeetTranscriber と共用
JUDGE_MODEL = 'claude-haiku-5-5'
API_KEY_FILES = [Path.home() / 'keys' / 'for_dictation.txt', Path.home() / 'keys' / 'claude.md']
JUDGE_MAX_MESSAGE_CHARS = 4000

JUDGE_SYSTEM = """あなたは、AIコーディングエージェント（Claude Code）がターンを終えた瞬間に、
「人間のユーザーがいま何かする必要があるか」を判定する分類器です。

与えられるもの:
- エージェントの最後のメッセージ
- まだ動いているバックグラウンドタスクの一覧

次の場合は NEEDS_USER:
- ユーザーに質問・確認・選択・承認を求めている
- 成果物が完成し、ユーザーが見る・試す・確認するべき状態になった（録画・ビルド・生成物の完成など）
- サーバーやアプリを新しく起動した、または変更を加えて再起動・更新した（ユーザーが動作確認する必要がある）
- 依頼された作業が終わり、ユーザーの次の指示を待っている

次の場合だけ NO_ACTION:
- バックグラウンド処理（録画、ビルド、長い計算など）の完了を待っていて、完了したらエージェント自身が
  続きを行うと述べており、それまでユーザーがすることは無い
- 以前から動いているサーバーなどがそのまま動いているだけで、今回のターンで新たに確認すべき変更が無い

迷ったら NEEDS_USER。出力は NEEDS_USER または NO_ACTION のどちらか一語だけ。"""


def has_active_background_tasks(data: dict) -> bool:
    """Stop 入力の background_tasks にまだ動いているものがあるか"""
    for t in data.get('background_tasks') or []:
        if not isinstance(t, dict):
            continue
        status = str(t.get('status') or '').lower()
        if status not in FINISHED_TASK_STATUSES:
            return True
    return False


def load_api_key():
    key = os.environ.get('ANTHROPIC_API_KEY')
    if key:
        return key
    for path in API_KEY_FILES:
        try:
            text = path.read_text(encoding='utf-8')
        except OSError:
            continue
        m = re.search(r'sk-ant-[A-Za-z0-9_\-]+', text)
        if m:
            return m.group(0)
    return None


def last_assistant_text(data: dict) -> str:
    """Stop 入力の last_assistant_message。無ければ transcript の末尾から拾う。"""
    text = data.get('last_assistant_message')
    if isinstance(text, str) and text.strip():
        return text
    path = data.get('transcript_path')
    if not path:
        return ''
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 200000))
            lines = f.read().decode('utf-8', errors='ignore').split('\n')
    except OSError:
        return ''
    for ln in reversed(lines):
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if d.get('type') != 'assistant':
            continue
        content = (d.get('message') or {}).get('content')
        if isinstance(content, list):
            parts = [b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text']
            if any(parts):
                return '\n'.join(parts)
    return ''


def judge_needs_user(data: dict):
    """Haiku に「ユーザーの行動が必要か」を聞く。判定できなければ None。"""
    api_key = load_api_key()
    if not api_key:
        return None, 'no_api_key'
    try:
        import anthropic
    except ImportError:
        return None, 'no_sdk'

    message = last_assistant_text(data)[-JUDGE_MAX_MESSAGE_CHARS:]
    tasks = [
        f"- [{t.get('type')}] {t.get('status')}: {t.get('description') or ''}"
        for t in (data.get('background_tasks') or []) if isinstance(t, dict)
    ]
    user_prompt = (
        "## エージェントの最後のメッセージ\n" + (message or '(なし)') +
        "\n\n## 動いているバックグラウンドタスク\n" + ('\n'.join(tasks) or '(なし)')
    )
    try:
        # フックの timeout(25秒) 内に必ず終わるよう、リトライ無し
        client = anthropic.Anthropic(api_key=api_key, timeout=15.0, max_retries=0)
        response = client.messages.create(
            model=JUDGE_MODEL,
            max_tokens=64,
            output_config={'effort': 'low'},
            system=JUDGE_SYSTEM,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
    except Exception as e:  # 判定失敗はフォールバックに任せる
        return None, f'api_error:{type(e).__name__}'
    if response.stop_reason == 'refusal':
        return None, 'refusal'
    answer = ''.join(b.text for b in response.content if b.type == 'text').strip().upper()
    if 'NEEDS_USER' in answer:
        return True, 'needs_user'
    if 'NO_ACTION' in answer:
        return False, 'no_action'
    return None, f'unparsed:{answer[:20]}'


def classify(data: dict):
    """フック入力から状態を返す: ('waiting'|'background'|'working'|'ended'|None, 判定理由)"""
    event = data.get('hook_event_name')
    if event == 'Stop':
        if not has_active_background_tasks(data):
            return 'waiting', 'no_background_tasks'
        needs_user, reason = judge_needs_user(data)
        if needs_user is None:
            # 判定できないときは「待ち」にしない（鳴らしすぎより鳴らさない方を選ぶ）
            return 'background', f'fallback:{reason}'
        return ('waiting' if needs_user else 'background'), f'judge:{reason}'
    if event == 'Notification':
        if data.get('notification_type') in WAITING_NOTIFICATIONS:
            return 'waiting', data.get('notification_type')
        return None, None
    if event in ('UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'SessionStart'):
        return 'working', event
    if event == 'SessionEnd':
        return 'ended', event
    return None, None


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    # ~/.ccmon/hook_debug が存在すれば生の入力を記録する（判定の調査用）
    if (CCMON_DIR / 'hook_debug').exists():
        try:
            with open(CCMON_DIR / 'hook_raw.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps(data, ensure_ascii=False) + '\n')
        except OSError:
            pass

    session_id = data.get('session_id')
    if not session_id:
        # transcript_path は .../<session_id>.jsonl なので、そこからも取れる
        session_id = Path(str(data.get('transcript_path') or '')).stem
    if not session_id:
        return
    # ファイル名に使えない文字を排除
    safe_id = ''.join(c for c in str(session_id) if c.isalnum() or c in '-_')
    if not safe_id:
        return

    state, reason = classify(data)
    if state is None:
        return

    path = STATE_DIR / f'{safe_id}.json'
    if state == 'ended':
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        'session_id': session_id,
        'state': state,
        'reason': reason,
        'event': data.get('hook_event_name'),
        'notification_type': data.get('notification_type'),
        'cwd': data.get('cwd'),
        'background_tasks': [
            {'type': t.get('type'), 'status': t.get('status'), 'description': t.get('description')}
            for t in (data.get('background_tasks') or []) if isinstance(t, dict)
        ],
        'ts': time.time(),
    }
    # 書きかけを ccmon に読まれないよう、一時ファイルに書いてから rename
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False)
    os.replace(tmp, path)


if __name__ == '__main__':
    main()
