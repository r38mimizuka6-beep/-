#!/usr/bin/env python3
"""trial cluster のログインページを Google Chrome で一発で開くスクリプト。"""

import os
import shutil
import sys
import webbrowser

URL = "https://trialclusterweb.com/portal/login"

# OSごとの Chrome の候補パス
CHROME_PATHS = [
    # macOS
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    # Windows
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
    ),
    # Linux
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]

# PATH 上で探す実行ファイル名
CHROME_COMMANDS = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
]


def find_chrome() -> str | None:
    """利用可能な Chrome の実行ファイルパスを返す。見つからなければ None。"""
    for path in CHROME_PATHS:
        if path and os.path.exists(path):
            return path
    for cmd in CHROME_COMMANDS:
        found = shutil.which(cmd)
        if found:
            return found
    return None


def main() -> int:
    print(f"Chrome で開いています: {URL}")

    chrome_path = find_chrome()
    if chrome_path is None:
        print(
            "Google Chrome が見つかりませんでした。"
            "\nChrome がインストールされているか確認してください。"
            "\n手動で開く場合は下記のURLをどうぞ:\n"
            f"{URL}",
            file=sys.stderr,
        )
        return 1

    # webbrowser に Chrome を登録して起動する
    # "%s" は開く URL に置き換えられる
    webbrowser.register(
        "chrome",
        None,
        webbrowser.BackgroundBrowser([chrome_path, "%s"]),
    )

    opened = webbrowser.get("chrome").open(URL)
    if not opened:
        print(
            "Chrome を起動できませんでした。"
            "\n手動で開く場合は下記のURLをどうぞ:\n"
            f"{URL}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
