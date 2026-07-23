#!/usr/bin/env python3
"""
Local script to push Chrome's CDW session cookies to Railway.
Connects to Chrome via the remote debugging port (CDP) to read live
in-memory cookies — the same cookies Cookie Editor sees.
"""
import os
import json
import time
import requests

RAILWAY_URL = "https://cdw-slack-bot-production.up.railway.app"
REFRESH_SECRET = os.environ.get("CDW_REFRESH_SECRET", "")
CDP_URL = "http://localhost:9222"


def get_cdw_cookies():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0]
        all_cookies = context.cookies()
        browser.disconnect()

    cdw_cookies = [c for c in all_cookies if "cdw.com" in c.get("domain", "")]

    # Normalize sameSite and drop -1 expiry (session cookies)
    for cookie in cdw_cookies:
        raw_same_site = cookie.get("sameSite", "")
        if raw_same_site not in ("None", "Lax", "Strict"):
            cookie["sameSite"] = "None"
        expires = cookie.get("expires", -1)
        if expires == -1:
            cookie.pop("expires", None)

    return cdw_cookies


def main():
    # Wait for Chrome and Keychain to be fully ready after login
    time.sleep(60)

    if not REFRESH_SECRET:
        print("ERROR: CDW_REFRESH_SECRET env var is not set.")
        return

    try:
        cookies = get_cdw_cookies()
    except Exception as e:
        print(f"ERROR: Could not connect to Chrome via CDP on {CDP_URL}: {e}")
        print("Make sure Chrome is running with --remote-debugging-port=9222")
        return

    if not cookies:
        print("ERROR: No CDW cookies found. Make sure you are logged into CDW in Chrome.")
        return

    try:
        resp = requests.post(
            f"{RAILWAY_URL}/internal/refresh-cookies",
            headers={"Authorization": f"Bearer {REFRESH_SECRET}"},
            json={"cookies": cookies},
            timeout=10,
        )
        if resp.status_code == 200:
            print(f"OK: Pushed {len(cookies)} CDW cookies to Railway.")
        else:
            print(f"ERROR: Railway returned {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"ERROR: Could not reach Railway: {e}")


if __name__ == "__main__":
    main()
