#!/bin/bash
# macOS: このファイルをダブルクリックすると起動します。
cd "$(dirname "$0")" || exit 1

fail() { echo; echo "$1"; echo; echo "Enter キーを押すと閉じます。"; read -r; exit 1; }

if [ ! -f app/dashboard.py ]; then
  fail "app/dashboard.py が見つかりません。start.command は shoken_analyzer フォルダの中に置いたままにしてください。"
fi

if [ ! -x .venv/bin/streamlit ]; then
  if [ ! -x .venv/bin/python ]; then
    echo "[1/3] 専用のPython環境を作成しています…"
    command -v python3 >/dev/null 2>&1 || fail "Python3 が見つかりません。https://www.python.org/downloads/ からインストールしてください。"
    python3 -m venv .venv || fail ".venv の作成に失敗しました。.venv フォルダを削除してからもう一度お試しください。"
  fi
  echo "[2/3] ライブラリをインストールしています（初回は数分かかります）…"
  ./.venv/bin/python -m pip install --upgrade pip >/dev/null
  ./.venv/bin/python -m pip install -r requirements.txt \
    || fail "ライブラリのインストールに失敗しました。社内ネットワークの制限の可能性があります（pypi.org への接続が必要です）。"
fi

echo "[3/3] ダッシュボードを起動します。ブラウザが開きます。"
echo "      終了するときはこのウィンドウを閉じてください。"
echo
./.venv/bin/python -m streamlit run app/dashboard.py
