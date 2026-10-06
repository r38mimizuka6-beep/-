@echo off
chcp 65001 >nul
rem Windows: このファイルをダブルクリックすると起動します。
cd /d "%~dp0"
if not exist ".venv" (
  echo 初回セットアップをしています（数分かかります）...
  python -m venv .venv
  if errorlevel 1 (
    echo Python が見つかりません。python.org からインストールし、
    echo インストール時に "Add python.exe to PATH" にチェックを入れてください。
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo ライブラリのインストールに失敗しました。社内ネットワークの制限かもしれません。
    pause
    exit /b 1
  )
)
echo ダッシュボードを起動します。ブラウザが開きます。
echo 終了するときはこのウィンドウを閉じてください。
".venv\Scripts\python.exe" -m streamlit run app/dashboard.py
pause
