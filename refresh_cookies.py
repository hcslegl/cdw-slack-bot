#!/usr/bin/env python3
"""
Local script to push Chrome's CDW session cookies to Railway.
Run on a schedule via launchd so the bot never loses its session.
"""
import os
import json
import time
import requests
import browser_cookie3

RAILWAY_URL = "https://cdw-slack-bot-production.up.railway.app"
REFRESH_SECRET = os.environ.get("CDW_REFRESH_SECRET", "")


def main():
    # Wait for Chrome and Keychain to be fully ready after login
    time.sleep(60)

    if not REFRESH_SECRET:
        print("ERROR: CDW_REFRESH_SECRET env var is not set.")
        return

    # Read CDW cookies from Chrome
    try:
        jar = browser_cookie3.chrome(domain_name=".cdw.com")
    except Exception as e:
        print(f"ERROR: Could not read Chrome cookies: {e}")
        return

    cookie_list = [
        {
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path,
            "secure": bool(c.secure),
            "httpOnly": False,
            "sameSite": "Lax",
        }
        for c in jar
    ]

    if not cookie_list:
        print("ERROR: No CDW cookies found in Chrome. Make sure you are logged into CDW.")
        return

    try:
        resp = requests.post(
            f"{RAILWAY_URL}/internal/refresh-cookies",
            headers={"Authorization": f"Bearer {REFRESH_SECRET}"},
            json={"cookies": cookie_list},
            timeout=10,
        )
        if resp.status_code == 200:
            print(f"OK: Pushed {len(cookie_list)} CDW cookies to Railway.")
        else:
            print(f"ERROR: Railway returned {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"ERROR: Could not reach Railway: {e}")


if __name__ == "__main__":
    main()
