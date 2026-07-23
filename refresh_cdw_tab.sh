#!/bin/bash
# Refreshes the CDW tab in Chrome every hour to keep the session alive.
# This prevents CDW from timing out the session due to inactivity,
# keeping the cookies pushed via /refreshsession valid indefinitely.
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
