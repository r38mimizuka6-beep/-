#!/usr/bin/env python3
"""Open the Notion meeting-notes database in Chrome and click "+ 新規ミーティング".

Requirements (first time only):
    pip install playwright

Chrome is started with a dedicated profile so it can be controlled
automatically. On the first run, log in to Notion in the window that opens;
the login is remembered for later runs.
"""

import os
import shutil
import subprocess
import sys
import time

URL = (
    "https://app.notion.com/p/3890c6890a5e80ba925ffe34cf55b9cb"
    "?v=3890c6890a5e80f0a436000ce3bbfc92"
)
DATABASE_ID = "3890c6890a5e80ba925ffe34cf55b9cb"
BUTTON_TEXT = "新規ミーティング"

DEBUG_PORT = 9223
PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".notion-new-meeting-chrome")

# Seconds to wait for the button before assuming we are not logged in
BUTTON_TIMEOUT = 20

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def find_chrome():
    """Return the path to a usable Chrome executable, or None if not found."""
    for path in CHROME_PATHS:
        if os.path.exists(path):
            return path
    for cmd in ("chrome", "google-chrome", "chromium"):
        found = shutil.which(cmd)
        if found:
            return found
    return None


def launch_chrome(chrome_path):
    """Start Chrome (or reuse the running one) with remote debugging enabled."""
    subprocess.Popen(
        [
            chrome_path,
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            URL,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def connect(playwright, timeout=30):
    """Connect to the Chrome started by launch_chrome, retrying until it is up."""
    deadline = time.time() + timeout
    while True:
        try:
            return playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{DEBUG_PORT}"
            )
        except Exception:
            if time.time() > deadline:
                raise
            time.sleep(0.5)


def find_notion_page(browser):
    """Return the tab showing the database, opening one if necessary."""
    context = browser.contexts[0]
    deadline = time.time() + 10
    while time.time() < deadline:
        for page in reversed(context.pages):
            if DATABASE_ID in page.url.replace("-", ""):
                return page
        time.sleep(0.5)
    page = context.new_page()
    page.goto(URL)
    return page


def click_new_meeting(page):
    """Click "+ 新規ミーティング". Return True on success."""
    button = page.get_by_text(BUTTON_TEXT, exact=True).last
    try:
        button.wait_for(state="visible", timeout=BUTTON_TIMEOUT * 1000)
    except Exception:
        return False
    page.bring_to_front()
    button.click()
    return True


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Playwright is not installed."
            "\nRun this once, then try again:"
            "\n    pip install playwright",
            file=sys.stderr,
        )
        return 1

    chrome_path = find_chrome()
    if chrome_path is None:
        print(
            "Google Chrome was not found."
            "\nPlease make sure Chrome is installed.",
            file=sys.stderr,
        )
        return 1

    print(f"Opening in Chrome: {URL}")
    launch_chrome(chrome_path)

    with sync_playwright() as p:
        browser = connect(p)
        page = find_notion_page(browser)

        if not click_new_meeting(page):
            print(
                "\nThe 'new meeting' button was not found."
                "\nIf Notion asks you to log in, log in in the Chrome window,"
                "\nthen come back here and press Enter."
            )
            input()
            page.goto(URL)
            if not click_new_meeting(page):
                print(
                    "Still could not find the button. Please click it manually.",
                    file=sys.stderr,
                )
                return 1

        print("New meeting page opened.")
        # Leaving the with-block only disconnects; the Chrome window stays open.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
