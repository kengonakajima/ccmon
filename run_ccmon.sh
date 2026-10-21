#!/bin/bash
# CCMon実行用スクリプト（クラッシュ時は自動再起動）

# スクリプトのディレクトリを取得
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# 仮想環境をアクティベート
source "$SCRIPT_DIR/venv/bin/activate"

# 短時間でのクラッシュが連続したら無限ループを避けるため停止する
FAST_CRASH_SECONDS=10
MAX_FAST_CRASHES=5
fast_crash_count=0

while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] CCMonを起動します..."
    START_TIME=$(date +%s)

    python3 "$SCRIPT_DIR/ccmon.py"
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] CCMonが正常に終了しました"
        break
    fi

    ELAPSED=$(( $(date +%s) - START_TIME ))
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] CCMonが異常終了しました (exit=$EXIT_CODE, 稼働${ELAPSED}秒)"

    if [ $ELAPSED -lt $FAST_CRASH_SECONDS ]; then
        fast_crash_count=$((fast_crash_count + 1))
        if [ $fast_crash_count -ge $MAX_FAST_CRASHES ]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] 起動直後のクラッシュが${MAX_FAST_CRASHES}回連続したため停止します"
            exit 1
        fi
    else
        fast_crash_count=0
    fi

    # クラッシュで端末がcbreak/カーソル非表示のまま残ることがあるためリセット
    [ -t 0 ] && stty sane 2>/dev/null
    tput cnorm 2>/dev/null

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 2秒後に再起動します..."
    sleep 2
done
