# CCMon - Claude/Codex/Cursor/Kimi/Agy Monitor

CCMonは、macOS用のClaude/Codex/Cursor/Kimi/Agyの活動状況を、ピコピコ音で表現するモニターツールです。

<img width="514" height="137" alt="image" src="https://github.com/user-attachments/assets/a6b8c6e6-5a6f-4563-b032-02324d271634" />

## 機能

- Claude Codeの会話ログ更新を検知して音で通知（`~/.claude/projects`）
- Codexのセッションログ更新を検知して音で通知（`~/.codex/sessions`）
- Cursorのチャットログ更新を検知して音で通知（`~/Library/Application Support/Cursor/User/workspaceStorage` / `globalStorage`）
- Kimi Codeのセッションログ更新を検知して音で通知（`~/.kimi-code/sessions`）
- Antigravity (agy) の会話ログ更新を検知して音で通知（`~/.gemini/antigravity-cli/brain` / `antigravity/brain` / `antigravity-ide/brain`）
- ランダムな音程でスパースな通知音を生成
- Claude Code / Codex / Agy がターン完了や質問でユーザーの入力を待っている状態になると、猫の鳴き声で通知
- Claude Code がユーザーの入力待ち（ターン完了・権限確認・質問）になると、ユーザーが操作するまで一定間隔でアラーム音を鳴らし続ける（フック連携）
- 異常終了時は `run_ccmon.sh` が自動で再起動
- FSEventsを使用した効率的なファイル監視

## 必要要件

- macOS
- Python 3.6以上
- Claude Code / Codex / Cursor / Opencode / Kimi Code CLI / Antigravity (agy) のいずれかがインストール済み

## インストール

1. リポジトリをクローン
```bash
git clone https://github.com/kengonakajima/ccmon.git
cd ccmon
```

2. 仮想環境を作成してパッケージをインストール
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 使い方

```bash
./run_ccmon.sh
```

または仮想環境をアクティベートして直接実行：

```bash
source venv/bin/activate
python3 ccmon.py
```

終了するには `Ctrl+C` を押してください。

### 「自分のターン」アラーム（Claude Code）

Claude Code のフックを使って、セッションが入力待ちになったことを ccmon に通知します。ログの推測ではなく Claude Code 自身のイベントを使うので、権限確認待ちも検出できます。

`~/.claude/settings.json` の `hooks` に、以下のイベントで `hooks/ccmon_state.py` を登録します：
`Stop`（ターン完了）、`Notification`（matcher: `permission_prompt|agent_needs_input|elicitation_dialog|elicitation_url_dialog`）、`UserPromptSubmit`・`PreToolUse`・`PostToolUse`・`SessionStart`（作業再開）、`SessionEnd`（終了）。

```json
{"type": "command", "command": "\"/path/to/ccmon/venv/bin/python3\" \"/path/to/ccmon/hooks/ccmon_state.py\"", "timeout": 25}
```

バックグラウンドタスク（録画、ビルド、開発サーバーなど）が動いたままターンが終わった場合だけ、Claude Haiku に最後のメッセージを渡して「ユーザーがいま何かすべきか」を判定させます（1回あたり数百トークン、約1〜4秒）。質問・確認・成果物の完成・新しく起動や更新したサーバーの動作確認なら鳴らし、完了待ちや以前から動いているだけのサーバーなら鳴らしません。API キーは `ANTHROPIC_API_KEY` または `~/keys/claude.md` から読みます。キーが無い・判定に失敗した場合は鳴らしません。バックグラウンドタスクが無ければ LLM は呼びません（`anthropic` パッケージ入りの `venv/bin/python3` でフックを動かしてください）。

フックは `~/.ccmon/state/<session_id>.json` に状態を書き、ccmon がそれを監視します。TUI の `Your turn:` 行に入力待ちセッションの作業ディレクトリ名が表示され、`s` キーでアラームを止められます（次の入力待ちで再び鳴ります）。

調整項目は `ccmon.py` 冒頭の定数で変更できます：

- `ALARM_INTERVAL_SECONDS`（既定10秒）: 鳴らす間隔
- `ALARM_MAX_SECONDS`（既定15分）: 1回の入力待ちで鳴らし続ける上限
- `ALARM_STOP_ON_USER_ACTIVITY`（既定 `True`）: 入力待ちになった後にキーボード・マウスを触ったら「気づいた」とみなしてそのアラームを止める。次の入力待ちで再び鳴る
- `ALARM_ONLY_WHEN_IDLE` / `ALARM_IDLE_SECONDS`: `True` にすると、キーボード・マウスを一定時間触っていない時だけ鳴らす
- アラーム音: `sounds/alarm.mp3`（`.aiff` / `.wav` も可）を置けばそれを使い、なければ macOS の `Glass.aiff`

### Mac操作時刻・アプリ名の活動ログ

時間配分（Mac操作中・AI待ち中・離席中）を後から集計できるよう、10秒間隔で `~/.ccmon/activity/YYYY-MM.jsonl` に軽量な活動ログを追記します。

- 記録内容: 時刻（`ts`/`iso`）、操作の有無（`active`）、無操作秒数（`idle_s`）、最終操作時刻（`last_input_ts`）、最前面アプリ名（`app`）、稼働中エージェント（`agents`：ファイル監視＋フックの `working` 状態を合流）、入力待ち（`waiting`）
- プライバシー配慮: キー入力の内容・URL・ウィンドウタイトルは一切記録しません（macOS の `HIDIdleTime` と最前面アプリ名のみ取得するため、アクセシビリティ権限も不要です）
- 低負荷設計: 完全離席かつAI停止が続く間は状態遷移と5分ごとのハートビートのみ書き込みます。無効化する場合は `CCMON_ACTIVITY_LOG=0` を指定してください。
- 集計・可視化コマンド:
  ```bash
  ./venv/bin/python3 report_activity.py               # 今月のサマリー表示＋PNG/SVGグラフ生成（~/.ccmon/reports/）
  ./venv/bin/python3 report_activity.py --days 7      # 直近7日間
  ./venv/bin/python3 report_activity.py --month 2026-11 --meetings ~/arkn/drafts/meetings-time-2026-11.csv
  ```

### 「自分のターン」アラーム（Antigravity / agy）

`agy` のフック機能（`~/.gemini/config/hooks.json`）に `hooks/ccmon_agy_state.py` を登録すると、以下のタイミングで Claude Code と同じ 10 秒おきアラームが鳴ります：

- ファイル編集（diff 許可待ち: `edit_file` / `write_file` 等）
- コマンド実行の許可待ち（`run_command`）
- 質問の回答待ち（`ask_question`）
- 会話のターン完了（`Stop`）

`~/.gemini/config/hooks.json` の設定例：

```json
{
  "ccmon-state": {
    "SessionStart": [
      { "type": "command", "command": "\"/path/to/ccmon/venv/bin/python3\" \"/path/to/ccmon/hooks/ccmon_agy_state.py\"", "timeout": 10 }
    ],
    "PreInvocation": [
      { "type": "command", "command": "\"/path/to/ccmon/venv/bin/python3\" \"/path/to/ccmon/hooks/ccmon_agy_state.py\"", "timeout": 10 }
    ],
    "PreToolUse": [
      {
        "matcher": "run_command|edit_file|write_file|write_to_file|replace_file_content|multi_replace_file_content|ask_question",
        "hooks": [
          { "type": "command", "command": "\"/path/to/ccmon/venv/bin/python3\" \"/path/to/ccmon/hooks/ccmon_agy_state.py\"", "timeout": 10 }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          { "type": "command", "command": "\"/path/to/ccmon/venv/bin/python3\" \"/path/to/ccmon/hooks/ccmon_agy_state.py\"", "timeout": 10 }
        ]
      }
    ],
    "Stop": [
      { "type": "command", "command": "\"/path/to/ccmon/venv/bin/python3\" \"/path/to/ccmon/hooks/ccmon_agy_state.py\"", "timeout": 10 }
    ]
  }
}
```

## 動作説明

CCMonは以下の活動を監視します：

- **ファイル監視**: 
  - Claude: `~/.claude/projects` 内の `.jsonl` 更新
  - Codex: `~/.codex/sessions` 内の `.jsonl`/`.json` 更新
  - Cursor: `~/Library/Application Support/Cursor/User/workspaceStorage` / `globalStorage` 内の `state.vscdb` / `state.vscdb-wal` 更新
  - Kimi: `~/.kimi-code/sessions` 内の `.jsonl` 更新
  - Agy: `~/.gemini/antigravity-cli/brain` / `~/.gemini/antigravity/brain` / `~/.gemini/antigravity-ide/brain` 内の `.system_generated/logs/transcript.jsonl` 更新

いずれかの活動を検知すると、10秒間ランダムな音程でビープ音を鳴らします。

また、Claude Code / Codex / Agy のログの最終行を確認し、ターンが完了してユーザーの入力を待っている状態（Claude Code / Agy では質問中も含む）を検知すると、ビープ音を止めて猫の鳴き声（`sounds/neko.mp3`）を1回鳴らします。

Codexでは、セッション情報で本体と補助エージェントを区別し、本体のターン完了だけを猫の鳴き声で通知します。guardianなどの内部エージェントやサブエージェントの完了では鳴りません。

Codexは実ログの追記でmacOSの変更通知が届かない場合があるため、毎秒ポーリングで監視します。他の監視にはFSEventsを使い、画面は状態や端末サイズが変わった時だけ再描画してCPU負荷を抑えます。
他の監視でも変更通知が届かない環境では、`CCMON_POLLING=1 ./run_ccmon.sh` で全対象を毎秒ポーリングに切り替えられます。

## 音の仕様

- macOS標準の`afplay`を別プロセスで使用（PyAudio / PortAudioは不要）
- 10秒分のビープと無音をまとめて再生し、プロセス起動による間隔の延びを防止
- 再生は1件ずつ処理し、予定の再生時間＋5秒で終了しないプロセスは停止。次の通知で再試行（猫の声は5秒で打ち切り）
- 周波数範囲: 400Hz〜1600Hz（ランダム）
- ビープ音の長さ: 0.05秒
- 無音期間: 0.2秒〜1.0秒（ランダム）
- 継続時間: 10秒間
- フェードアウト付きでプチプチ音を防止

## クレジット

- 猫の鳴き声（`sounds/neko.mp3`）: [効果音ラボ](https://soundeffect-lab.info/)

## ライセンス

MIT License
