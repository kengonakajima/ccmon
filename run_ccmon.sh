#!/bin/bash
# CCMon実行用スクリプト（30秒ごとに自動再起動）

# スクリプトのディレクトリを取得
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# 仮想環境をアクティベート
source "$SCRIPT_DIR/venv/bin/activate"

# 無限ループで30秒ごとに再起動
while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] CCMonを起動します..."

    # タイムアウトコマンド検出（macOSは通常gtimeoutが必要）
    TIMEOUT_CMD=""
    FOREGROUND_FLAG=""
    if command -v gtimeout >/dev/null 2>&1; then
        TIMEOUT_CMD="gtimeout"
        FOREGROUND_FLAG="--foreground"
    elif command -v timeout >/dev/null 2>&1; then
        TIMEOUT_CMD="timeout"
        # 一部のtimeoutは --foreground 非対応
        if $TIMEOUT_CMD --help 2>&1 | grep -q -- "--foreground"; then
            FOREGROUND_FLAG="--foreground"
        fi
    fi

    if [ -n "$TIMEOUT_CMD" ]; then
        $TIMEOUT_CMD $FOREGROUND_FLAG 30 python3 "$SCRIPT_DIR/ccmon.py"
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] timeoutコマンドが見つかりません。制限なしで起動します（Ctrl+Cで終了）。"
        python3 "$SCRIPT_DIR/ccmon.py"
    fi
    
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
