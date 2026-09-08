# CCMon - Claude/Codex/Cursor/Kimi Monitor

CCMonは、macOS用のClaude/Codex/Cursor/Kimiの活動状況を、ピコピコ音で表現するモニターツールです。

<img width="514" height="137" alt="image" src="https://github.com/user-attachments/assets/a6b8c6e6-5a6f-4563-b032-02324d271634" />

## 機能

- Claude Codeの会話ログ更新を検知して音で通知（`~/.claude/projects`）
- Codexのセッションログ更新を検知して音で通知（`~/.codex/sessions`）
- Cursorのチャットログ更新を検知して音で通知（`~/Library/Application Support/Cursor/User/workspaceStorage` / `globalStorage`）
- Kimi Codeのセッションログ更新を検知して音で通知（`~/.kimi-code/sessions`）
- ランダムな音程でスパースな通知音を生成
- Claude Code / Codex がターン完了や質問でユーザーの入力を待っている状態になると、猫の鳴き声で通知
- FSEventsを使用した効率的なファイル監視

## 必要要件

- macOS
- Python 3.6以上
- PortAudio（PyAudioの依存）
- Claude Code / Codex / Cursor / Opencode / Kimi Code CLI のいずれかがインストール済み

## インストール

1. PortAudioをインストール
```bash
brew install portaudio
```

2. リポジトリをクローン
```bash
git clone https://github.com/kengonakajima/ccmon.git
cd ccmon
```

3. 仮想環境を作成してパッケージをインストール
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

## 動作説明

CCMonは以下の活動を監視します：

- **ファイル監視**: 
  - Claude: `~/.claude/projects` 内の `.jsonl` 更新
  - Codex: `~/.codex/sessions` 内の `.jsonl`/`.json` 更新
  - Cursor: `~/Library/Application Support/Cursor/User/workspaceStorage` / `globalStorage` 内の `state.vscdb` / `state.vscdb-wal` 更新
  - Kimi: `~/.kimi-code/sessions` 内の `.jsonl` 更新

いずれかの活動を検知すると、10秒間ランダムな音程でビープ音を鳴らします。

また、Claude Code / Codex のログの最終行を確認し、ターンが完了してユーザーの入力を待っている状態（Claude Codeでは質問中も含む）を検知すると、ビープ音を止めて猫の鳴き声（`sounds/neko.mp3`）を1回鳴らします。

Codexでは、セッション情報で本体と補助エージェントを区別し、本体のターン完了だけを猫の鳴き声で通知します。guardianなどの内部エージェントやサブエージェントの完了では鳴りません。

Codexは実ログの追記でmacOSの変更通知が届かない場合があるため、毎秒ポーリングで監視します。他の監視にはFSEventsを使い、画面は状態や端末サイズが変わった時だけ再描画してCPU負荷を抑えます。
他の監視でも変更通知が届かない環境では、`CCMON_POLLING=1 ./run_ccmon.sh` で全対象を毎秒ポーリングに切り替えられます。

## 音の仕様

- 周波数範囲: 400Hz〜1600Hz（ランダム）
- ビープ音の長さ: 0.05秒
- 無音期間: 0.2秒〜1.0秒（ランダム）
- 継続時間: 10秒間
- フェードアウト付きでプチプチ音を防止

## クレジット

- 猫の鳴き声（`sounds/neko.mp3`）: [効果音ラボ](https://soundeffect-lab.info/)

## ライセンス

MIT License
