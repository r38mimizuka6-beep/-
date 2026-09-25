#!/usr/bin/env python3
"""Open the trial cluster login page in Google Chrome."""

import os
import shutil
import sys
import webbrowser

URL = "https://trialclusterweb.com/portal/login"

# Chrome candidate paths per OS
CHROME_PATHS = [
    # macOS
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    # Windows
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    # Linux
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]

# Executable names to look up on PATH
CHROME_COMMANDS = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
]


def find_chrome():
    """Return the path to a usable Chrome executable, or None if not found."""
    for path in CHROME_PATHS:
        if path and os.path.exists(path):
            return path
    for cmd in CHROME_COMMANDS:
        found = shutil.which(cmd)
        if found:
            return found
    return None


def main():
    print(f"Opening in Chrome: {URL}")

    chrome_path = find_chrome()
    if chrome_path is None:
        print(
            "Google Chrome was not found."
            "\nPlease make sure Chrome is installed."
            "\nTo open manually, use this URL:\n"
            f"{URL}",
            file=sys.stderr,
        )
        return 1

    webbrowser.register(
        "chrome",
        None,
        webbrowser.BackgroundBrowser([chrome_path, "%s"]),
    )

    opened = webbrowser.get("chrome").open(URL)
    if not opened:
        print(
            "Failed to launch Chrome."
            "\nTo open manually, use this URL:\n"
            f"{URL}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
