#!/bin/bash
# Quits Chrome if running, then relaunches with remote debugging port enabled.
# Run this once during setup, then the launchd job handles it on future logins.

echo "Quitting Chrome..."
osascript -e 'tell application "Google Chrome" to quit'
sleep 5

echo "Launching Chrome with remote debugging port 9222..."
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
    --remote-debugging-port=9222 \
    --restore-last-session \
    > /tmp/cdw-chrome-debug.log 2>&1 &

echo "Done. Chrome is starting with --remote-debugging-port=9222."
echo "Log back into CDW in Chrome, then the cookie refresh will work automatically."
