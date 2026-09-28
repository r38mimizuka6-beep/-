#!/usr/bin/env python3
"""Open the trial cluster login page in Google Chrome and log in automatically.

Credentials are read from "password.txt" in the "python" folder on the Desktop:
    line 1: user ID
    line 2: password

Requirements:
    pip install selenium   (4.6 or later; it downloads chromedriver automatically)
"""

import os
import sys
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

URL = os.environ.get("TRIAL_CLUSTER_URL", "https://trialclusterweb.com/portal/login")
CREDENTIAL_FILENAME = "password.txt"
TIMEOUT_SEC = 20

# Chrome candidate paths per OS (used only if Selenium cannot find Chrome by itself)
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

# Selectors tried in order to locate each element on the login form
USER_SELECTORS = [
    "input[name='loginId']",
    "input[name='userId']",
    "input[name='username']",
    "input[name='login_id']",
    "input[type='text']",
    "input[type='email']",
    "input:not([type])",
]
PASSWORD_SELECTORS = ["input[type='password']"]
SUBMIT_XPATHS = [
    "//button[contains(normalize-space(.), '\u30ed\u30b0\u30a4\u30f3')]",  # "login" in Japanese
    "//input[(@type='submit' or @type='button') and contains(@value, '\u30ed\u30b0\u30a4\u30f3')]",
    "//button[@type='submit']",
    "//input[@type='submit']",
]


def credential_candidates():
    """Return the possible locations of password.txt (Desktop/python/password.txt)."""
    home = Path.home()
    desktops = [home / "Desktop"]
    # On Windows the Desktop is often redirected into OneDrive
    for env in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        onedrive = os.environ.get(env)
        if onedrive:
            desktops.append(Path(onedrive) / "Desktop")
    desktops.append(home / "OneDrive" / "Desktop")
    desktops.append(home / "\u30c7\u30b9\u30af\u30c8\u30c3\u30d7")  # "Desktop" in Japanese
    return [d / "python" / CREDENTIAL_FILENAME for d in desktops]


def load_credentials():
    """Read (user_id, password) from password.txt. Line 1 = ID, line 2 = password."""
    override = os.environ.get("TRIAL_CLUSTER_CREDENTIALS")
    candidates = [Path(override)] if override else credential_candidates()
    for path in candidates:
        if path.is_file():
            break
    else:
        raise FileNotFoundError(
            "password.txt was not found. Looked in:\n  "
            + "\n  ".join(str(p) for p in candidates)
        )

    # utf-8-sig strips the BOM that Windows Notepad may add; fall back to cp932
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp932")

    # Only strip line endings: the password may contain spaces or symbols
    lines = [line.rstrip("\r\n") for line in text.splitlines()]
    if len(lines) < 2 or not lines[0].strip() or not lines[1]:
        raise ValueError(f"{path} must have the ID on line 1 and the password on line 2.")
    return lines[0].strip(), lines[1], path


def find_chrome_binary():
    for path in CHROME_PATHS:
        if path and os.path.exists(path):
            return path
    return None


def create_driver():
    options = webdriver.ChromeOptions()
    # Keep the Chrome window open after this script exits
    options.add_experimental_option("detach", True)
    if os.environ.get("TRIAL_CLUSTER_HEADLESS") == "1":
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
    binary = find_chrome_binary()
    if binary:
        options.binary_location = binary
    return webdriver.Chrome(options=options)


def find_first(driver, by, selectors):
    """Return the first visible element matching any selector, or None."""
    for selector in selectors:
        for element in driver.find_elements(by, selector):
            if element.is_displayed() and element.is_enabled():
                return element
    return None


def fill(element, value):
    element.clear()  # Chrome autofill may have filled the field already
    element.send_keys(value)


def login(driver, user_id, password):
    wait = WebDriverWait(driver, TIMEOUT_SEC)
    driver.get(URL)

    password_box = wait.until(
        lambda d: find_first(d, By.CSS_SELECTOR, PASSWORD_SELECTORS)
    )
    user_box = find_first(driver, By.CSS_SELECTOR, USER_SELECTORS)
    if user_box is None:
        raise RuntimeError("Could not find the ID input field.")

    fill(user_box, user_id)
    fill(password_box, password)

    submit = find_first(driver, By.XPATH, SUBMIT_XPATHS)
    if submit is not None:
        submit.click()
    else:
        password_box.submit()

    # Success = we leave the login page (URL changes or the password field disappears)
    try:
        wait.until(
            lambda d: "/login" not in d.current_url
            or not d.find_elements(By.CSS_SELECTOR, "input[type='password']")
        )
    except TimeoutException:
        return False
    return True


def main():
    try:
        user_id, password, cred_path = load_credentials()
    except (OSError, ValueError) as e:
        print(f"Failed to read credentials: {e}", file=sys.stderr)
        return 1
    print(f"Loaded credentials from: {cred_path} (ID: {user_id})")
    print(f"Opening in Chrome: {URL}")

    try:
        driver = create_driver()
    except WebDriverException as e:
        print(
            "Failed to launch Chrome via Selenium."
            "\nMake sure Google Chrome is installed and run: pip install -U selenium"
            f"\nDetails: {e.msg}",
            file=sys.stderr,
        )
        return 1

    try:
        ok = login(driver, user_id, password)
    except (TimeoutException, RuntimeError) as e:
        print(f"Could not find the login form: {e}", file=sys.stderr)
        return 1

    if ok:
        print(f"Login succeeded: {driver.current_url}")
        return 0
    print(
        "Login did not complete (still on the login page)."
        "\nPlease check the ID / password in password.txt.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
