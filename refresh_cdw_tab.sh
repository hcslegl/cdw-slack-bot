#!/bin/bash
# Refreshes the CDW tab in Chrome to keep the session alive
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
