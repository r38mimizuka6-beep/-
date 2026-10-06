#!/bin/bash
# macOS: このファイルをダブルクリックすると起動します。
cd "$(dirname "$0")" || exit 1
if [ ! -d .venv ]; then
  echo "初回セットアップをしています（数分かかります）…"
  python3 -m venv .venv || { echo "Python3 が見つかりません。python.org からインストールしてください。"; read -r; exit 1; }
  ./.venv/bin/python -m pip install --upgrade pip >/dev/null
  ./.venv/bin/python -m pip install -r requirements.txt || { echo "ライブラリのインストールに失敗しました。"; read -r; exit 1; }
fi
echo "ダッシュボードを起動します。ブラウザが開きます。"
echo "終了するときはこのウィンドウを閉じてください。"
./.venv/bin/python -m streamlit run app/dashboard.py
