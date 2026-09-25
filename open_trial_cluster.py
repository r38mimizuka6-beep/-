#!/usr/bin/env python3
"""trial cluster のログインページを既定のブラウザで一発で開くスクリプト。"""

import sys
import webbrowser

URL = "https://trialclusterweb.com/portal/login"


def main() -> int:
    print(f"ブラウザで開いています: {URL}")
    opened = webbrowser.open(URL)
    if not opened:
        # ブラウザを起動できなかった場合（ヘッドレス環境など）
        print(
            "既定のブラウザを起動できませんでした。"
            "\n下記のURLを手動で開いてください:\n"
            f"{URL}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
