#!/bin/bash
# CCMon実行用スクリプト（30秒ごとに自動再起動）

# スクリプトのディレクトリを取得
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# 仮想環境をアクティベート
source "$SCRIPT_DIR/venv/bin/activate"

# 無限ループで30秒ごとに再起動
while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] CCMonを起動します..."
    
    # CCMonを30秒間実行（タイムアウト付き、フォアグラウンドで実行）
    timeout --foreground 30 python3 "$SCRIPT_DIR/ccmon.py"
    
    # 終了コードを確認
    EXIT_CODE=$?
    
    # Ctrl+Cで終了した場合はループを抜ける（130のみ）
    if [ $EXIT_CODE -eq 130 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ユーザーによる終了を検知しました"
        break
    fi
    
    # タイムアウト（124）またはエラー終了（1）の場合は継続
    if [ $EXIT_CODE -eq 124 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 30秒経過 - 再起動します"
    elif [ $EXIT_CODE -eq 1 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] エラー終了を検知 - 再起動します"
    fi
    
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] CCMonを再起動します（30秒経過）"
    sleep 1  # 短い待機時間を入れて連続起動を防ぐ
done

echo "CCMonスクリプトを終了しました"