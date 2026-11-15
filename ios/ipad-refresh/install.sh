#!/bin/bash
# Installs (or updates) the launchd job that keeps Lute signed on the iPad.
#
# Usage:
#   install.sh [--days N] [--device UDID] [--team ID]   install, or change the interval
#   install.sh --uninstall                  remove the job
#
# The job checks every hour and rebuilds once N days (default 2) have passed,
# or sooner if the signing certificate is revoked. With no --device, the one
# paired physical iPad is used.
#
# launchd runs a small helper app (~/Applications/Lute iPad Refresh.app)
# rather than bash, because macOS blocks background jobs from ~/Documents.
# Give the helper Full Disk Access once in System Settings. It is only
# rebuilt when its source or the script path changes, since a rebuild
# changes its signature and macOS then asks for the access again.

set -euo pipefail

LABEL="app.lute.ipad-refresh"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
STATE="$HOME/Library/Application Support/lute-ipad-refresh"
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/refresh.sh"
HELPER_APP="$HOME/Applications/Lute iPad Refresh.app"
HELPER="$HELPER_APP/Contents/MacOS/lute-ipad-refresh"

INTERVAL_DAYS=2
DEVICE=""
TEAM="UZZTBVTKA4"
[ -f "$STATE/config" ] && source "$STATE/config"

while [ $# -gt 0 ]; do
	case "$1" in
		--days) INTERVAL_DAYS="$2"; shift 2 ;;
		--device) DEVICE="$2"; shift 2 ;;
		--team) TEAM="$2"; shift 2 ;;
		--uninstall)
			launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
			rm -f "$PLIST"
			rm -rf "$HELPER_APP"
			echo "Removed $LABEL and its helper app."
			exit 0 ;;
		*) echo "Unknown option: $1" >&2; exit 1 ;;
	esac
done

if ! [[ "$INTERVAL_DAYS" =~ ^[1-6]$ ]]; then
	echo "--days must be 1–6: a free Personal Team signature lasts 7 days." >&2
	exit 1
fi

if [ -z "$DEVICE" ]; then
	json=$(mktemp)
	xcrun devicectl list devices --json-output "$json" >/dev/null 2>&1
	DEVICE=$(python3 -I -c '
import json, sys
devices = json.load(open(sys.argv[1]))["result"]["devices"]
ipads = [d["hardwareProperties"]["udid"] for d in devices
         if d["hardwareProperties"].get("deviceType") == "iPad"
         and d["hardwareProperties"].get("reality") == "physical"]
print(ipads[0] if len(ipads) == 1 else "")
' "$json")
	rm -f "$json"
	if [ -z "$DEVICE" ]; then
		echo "Couldn't pick a single paired iPad; pass --device UDID." >&2
		exit 1
	fi
fi

mkdir -p "$STATE" "$(dirname "$PLIST")"

stamp="$(shasum "$HERE/helper.c" | cut -d' ' -f1) $SCRIPT"
if [ "$(cat "$HELPER_APP/Contents/Resources/stamp" 2>/dev/null)" != "$stamp" ]; then
	rm -rf "$HELPER_APP"
	mkdir -p "$HELPER_APP/Contents/MacOS" "$HELPER_APP/Contents/Resources"
	cat > "$HELPER_APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleIdentifier</key>
	<string>$LABEL.helper</string>
	<key>CFBundleName</key>
	<string>Lute iPad Refresh</string>
	<key>CFBundleExecutable</key>
	<string>lute-ipad-refresh</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>LSBackgroundOnly</key>
	<true/>
</dict>
</plist>
EOF
	clang -O2 -DSCRIPT="\"$SCRIPT\"" -o "$HELPER" "$HERE/helper.c"
	echo "$stamp" > "$HELPER_APP/Contents/Resources/stamp"
	codesign --force --sign - --identifier "$LABEL.helper" "$HELPER_APP"
	echo "Built the helper. Give it Full Disk Access: System Settings ▸ Privacy & Security ▸"
	echo "Full Disk Access ▸ + ▸ your home folder's Applications ▸ Lute iPad Refresh."
	open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
fi

cat > "$STATE/config" <<EOF
INTERVAL_DAYS=$INTERVAL_DAYS
DEVICE=$DEVICE
TEAM=$TEAM
EOF

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>$HELPER</string>
	</array>
	<key>StartInterval</key>
	<integer>3600</integer>
	<key>RunAtLoad</key>
	<true/>
	<key>StandardErrorPath</key>
	<string>$HOME/Library/Logs/lute-ipad-refresh.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL: refresh every $INTERVAL_DAYS days for iPad $DEVICE."
echo "Log: ~/Library/Logs/lute-ipad-refresh.log"
