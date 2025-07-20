# CCMon - Claude Code Monitor

CCMonは、macOS用のClaude Codeの活動状況を音で表現するモニターツールです。

## 機能

- Claude Codeの会話ログ更新を検知して音で通知
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

1. **ファイル監視**: `~/.claude/projects` ディレクトリ内の .jsonl ファイルの更新
2. **プロセス監視**: `claude` コマンドの実行状態（CPU使用中かどうか）

いずれかの活動を検知すると、10秒間ランダムな音程でビープ音を鳴らします。

## 音の仕様

- 周波数範囲: 400Hz〜1600Hz（ランダム）
- ビープ音の長さ: 0.05秒
- 無音期間: 0.2秒〜1.0秒（ランダム）
- 継続時間: 10秒間
- フェードアウト付きでプチプチ音を防止

## ライセンス

MIT License