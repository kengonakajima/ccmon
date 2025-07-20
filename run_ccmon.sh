#!/bin/bash
# CCMon実行用スクリプト

# スクリプトのディレクトリを取得
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# 仮想環境をアクティベート
source "$SCRIPT_DIR/venv/bin/activate"

# CCMonを実行
python3 "$SCRIPT_DIR/ccmon.py"