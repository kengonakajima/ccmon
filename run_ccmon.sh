#!/bin/bash
# CCMon実行用スクリプト（単発実行）

# スクリプトのディレクトリを取得
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# 仮想環境をアクティベート
source "$SCRIPT_DIR/venv/bin/activate"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] CCMonを起動します..."

python3 "$SCRIPT_DIR/ccmon.py"
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] CCMonが正常に終了しました"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] CCMonが異常終了しました (exit=$EXIT_CODE)"
fi
