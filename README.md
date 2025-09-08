# CCMon - Claude/Codex/Cursor Monitor

CCMonは、macOS用のClaude/Codex/Cursorの活動状況を、ピコピコ音で表現するモニターツールです。

<img width="528" height="351" alt="image" src="https://github.com/user-attachments/assets/30d72359-21d4-4a46-bc1f-beac12fb1184" />

## 機能

- Claude Codeの会話ログ更新を検知して音で通知（`~/.claude/projects`）
- Codexのセッションログ更新を検知して音で通知（`~/.codex/sessions`）
- Cursorのチャットログ更新を検知して音で通知（`~/.cursor/chats`）
- Claudeプロセスの実行状態を監視
- ランダムな音程でスパースな通知音を生成
- FSEventsを使用した効率的なファイル監視

## 必要要件

- macOS
- Python 3.6以上
- PortAudio（PyAudioの依存）
- Claude Code CLIがインストール済み

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

CCMonは以下の2つの活動を監視します：

1. **ファイル監視**: 
   - Claude: `~/.claude/projects` 内の `.jsonl` 更新
   - Codex: `~/.codex/sessions` 内の `.jsonl`/`.json` 更新
   - Cursor: `~/.cursor/chats` 内のファイル作成/更新（拡張子不問）
2. **プロセス監視**: `claude`/`codex`/`cursor-agent` のネットワーク活動

いずれかの活動を検知すると、10秒間ランダムな音程でビープ音を鳴らします。

## 音の仕様

- 周波数範囲: 400Hz〜1600Hz（ランダム）
- ビープ音の長さ: 0.05秒
- 無音期間: 0.2秒〜1.0秒（ランダム）
- 継続時間: 10秒間
- フェードアウト付きでプチプチ音を防止

## ライセンス

MIT License
