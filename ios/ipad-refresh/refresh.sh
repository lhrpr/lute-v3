#!/bin/bash
# Rebuilds Lute and reinstalls it on the iPad, so the free Personal Team
# signature (7 days) never runs out. launchd runs this every hour (see
# install.sh); it only does the work when a refresh is due:
#   - INTERVAL_DAYS have passed since the last successful install, or
#   - the certificate the installed copy was signed with has been revoked.
# A run that can't reach the iPad, or can't install because it is locked,
# records nothing, so the next hourly run tries again.
#
# Each refresh also runs `briefcase update iOS`, so the iPad picks up the
# current working tree of ../../lute.
#
# Usage: refresh.sh [--force]

set -uo pipefail

IOSDIR="$(cd "$(dirname "$0")/.." && pwd)"
XCODEPROJ="$IOSDIR/build/luteios/ios/xcode/Lute.xcodeproj"
STATE="$HOME/Library/Application Support/lute-ipad-refresh"
LOG="$HOME/Library/Logs/lute-ipad-refresh.log"
PROFILES="$HOME/Library/Developer/Xcode/UserData/Provisioning Profiles"

INTERVAL_DAYS=2
DEVICE=""
TEAM=""
# Must match the CFBundleIdentifier Briefcase writes into Info.plist
# (bundle + app name in ../pyproject.toml), or the install fails with
# "invalid entitlements".
BUNDLE_ID="org.lute.luteios"
[ -f "$STATE/config" ] && source "$STATE/config"

mkdir -p "$STATE" "$(dirname "$LOG")"
DERIVED="$STATE/DerivedData"
APP="$DERIVED/Build/Products/Debug-iphoneos/Lute.app"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }
notify() { osascript -e "display notification \"$1\" with title \"Lute iPad refresh\"" >/dev/null 2>&1; }

if [ -z "$DEVICE" ] || [ -z "$TEAM" ]; then
	log "No DEVICE/TEAM in $STATE/config; run install.sh first."
	exit 1
fi

# One run at a time: a slow build can outlast the hourly interval.
LOCK="$STATE/lock"
if ! mkdir "$LOCK" 2>/dev/null; then
	exit 0
fi
trap 'rmdir "$LOCK"' EXIT

now=$(date +%s)
last=$(cat "$STATE/last-success" 2>/dev/null || echo 0)
cert=$(cat "$STATE/last-cert" 2>/dev/null || echo "")
expires=$(cat "$STATE/last-expiry" 2>/dev/null || echo 0)

reason=""
if [ "${1:-}" = "--force" ]; then
	reason="forced"
elif [ "$last" -eq 0 ]; then
	reason="no previous install"
elif [ $((now - last)) -ge $((INTERVAL_DAYS * 86400)) ]; then
	reason="$INTERVAL_DAYS days since the last install"
elif [ -n "$cert" ] && security find-identity -v -p codesigning | grep "$cert" | grep -q REVOKED; then
	reason="signing certificate revoked"
fi
[ -z "$reason" ] && exit 0

if ! xcrun devicectl device info lockState --device "$DEVICE" >/dev/null 2>&1; then
	log "Refresh due ($reason), but the iPad isn't reachable; will retry."
	# Warn once the installed copy is within a day of expiring.
	if [ "$expires" -gt 0 ] && [ $((expires - now)) -lt 86400 ]; then
		notify "Lute expires on the iPad within a day. Wake the iPad on the same Wi-Fi as this Mac."
	fi
	exit 0
fi

log "Refreshing ($reason)."

# Copy the current lute/ sources into the Xcode project.
if ! (cd "$IOSDIR" && ./.venv/bin/briefcase update iOS --no-input) > "$STATE/update.log" 2>&1; then
	log "briefcase update failed; building the previous sources. See $STATE/update.log"
fi

build() {
	xcodebuild -project "$XCODEPROJ" -scheme Lute -configuration Debug \
		-destination "id=$DEVICE" -derivedDataPath "$DERIVED" -allowProvisioningUpdates \
		DEVELOPMENT_TEAM="$TEAM" CODE_SIGN_STYLE=Automatic PRODUCT_BUNDLE_IDENTIFIER="$BUNDLE_ID" \
		build > "$STATE/build.log" 2>&1
}

profile_expiry() {
	security cms -D -i "$APP/embedded.mobileprovision" 2>/dev/null \
		| plutil -extract ExpirationDate raw -o - - 2>/dev/null \
		| xargs -I{} date -j -u -f '%Y-%m-%dT%H:%M:%SZ' {} +%s 2>/dev/null
}

if ! build; then
	log "Build failed; see $STATE/build.log"
	grep -E "error:" "$STATE/build.log" | head -5 >> "$LOG"
	notify "Building Lute failed. If Xcode lost its account, sign in again in Xcode ▸ Settings ▸ Accounts."
	exit 1
fi

# Xcode reuses a cached profile until it is nearly expired, so a rebuild alone
# may not push the expiry out. If this one wouldn't last until the next
# refresh, drop it from the cache and build again for a fresh one.
expiry=$(profile_expiry)
if [ -n "$expiry" ] && [ $((expiry - now)) -lt $(((INTERVAL_DAYS + 1) * 86400)) ]; then
	uuid=$(security cms -D -i "$APP/embedded.mobileprovision" | plutil -extract UUID raw -o - -)
	log "Profile expires $(date -r "$expiry"); requesting a new one."
	rm -f "$PROFILES/$uuid.mobileprovision"
	if ! build; then
		log "Rebuild for a new profile failed; see $STATE/build.log"
		exit 1
	fi
	expiry=$(profile_expiry)
fi

if ! xcrun devicectl device install app --device "$DEVICE" "$APP" > "$STATE/install.log" 2>&1; then
	log "Install failed (is the iPad locked?); will retry. See $STATE/install.log"
	exit 1
fi

tmp=$(mktemp -d)
(cd "$tmp" && codesign -d --extract-certificates "$APP" 2>/dev/null)
sha1=$(openssl x509 -inform der -in "$tmp/codesign0" -noout -fingerprint -sha1 | sed 's/.*=//; s/://g')
rm -rf "$tmp"

echo "$now" > "$STATE/last-success"
echo "$sha1" > "$STATE/last-cert"
echo "${expiry:-0}" > "$STATE/last-expiry"
log "Installed; signature valid until $(date -r "${expiry:-0}")."
