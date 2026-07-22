#!/bin/bash
# Refreshes the CDW tab in Chrome to keep the session alive,
# then immediately pushes the updated cookies to Railway.
osascript <<'EOF'
tell application "Google Chrome"
    repeat with w in windows
        repeat with t in tabs of w
            if URL of t contains "cdw.com" then
                reload t
            end if
        end repeat
    end repeat
end tell
EOF

# Wait for Chrome to receive fresh cookies from CDW after the page reloads
sleep 10

# Push the fresh cookies to Railway
/usr/bin/python3 /Users/toast-it-devices/cdw-slack-bot/refresh_cookies.py
