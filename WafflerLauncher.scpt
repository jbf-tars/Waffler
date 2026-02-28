#!/usr/bin/osascript
# Waffler AppleScript Launcher
# Launches Waffler.app via its executable to bypass Gatekeeper

do shell script "/Applications/Waffler.app/Contents/MacOS/Waffler > /dev/null 2>&1 &"
