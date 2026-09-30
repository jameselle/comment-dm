#!/bin/sh
# Runs comment-dm in the background on this Mac (launchd), live, and refreshes the Instagram token weekly.
#   ./install-service.sh          install and start
#   ./install-service.sh remove   stop and remove
# Only install after a dry run and a live test with the allowlist look right.
set -eu
cd "$(dirname "$0")"
DIR="$(pwd)"
AGENTS="$HOME/Library/LaunchAgents"
RUN="$AGENTS/com.commentdm.run.plist"
REFRESH="$AGENTS/com.commentdm.refresh.plist"
mkdir -p "$AGENTS" logs

if [ "${1:-}" = "remove" ]; then
  launchctl bootout "gui/$(id -u)" "$RUN" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)" "$REFRESH" 2>/dev/null || true
  rm -f "$RUN" "$REFRESH"
  echo "removed"; exit 0
fi

[ -f config.json ] || { echo "No config.json yet: copy config.example.json and edit it."; exit 1; }
python3 -m commentdm check >/dev/null || { echo "The token check failed: run python3 -m commentdm setup first."; exit 1; }

cat > "$RUN" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.commentdm.run</string>
  <key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>-m</string><string>commentdm</string><string>run</string><string>--live</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$DIR/logs/run.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/run.log</string>
</dict></plist>
PLIST

cat > "$REFRESH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.commentdm.refresh</string>
  <key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>-m</string><string>commentdm</string><string>refresh-token</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartCalendarInterval</key><dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$DIR/logs/refresh.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/refresh.log</string>
</dict></plist>
PLIST

plutil -lint "$RUN" "$REFRESH" >/dev/null
launchctl bootout "gui/$(id -u)" "$RUN" 2>/dev/null || true
launchctl bootout "gui/$(id -u)" "$REFRESH" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$RUN"
launchctl bootstrap "gui/$(id -u)" "$REFRESH"
echo "running live in the background. Log: $DIR/logs/run.log   Stop: ./install-service.sh remove"
