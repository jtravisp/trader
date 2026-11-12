#!/bin/bash
# macOS scheduler: the launchd equivalent of scripts/register-tasks.ps1.
#   trader-forward : daily 19:00 ET  - forward data + LLM scoring, daily report, tomorrow's target
#   trader-gateway : Mon-Fri 09:40 ET - risk gateway submits the target to the PAPER account (D9, D19)
# Both run `uv run --no-sync trader job <name>` (src/trader/jobs.py), which logs to data/logs/<name>_<date>.log.
#
# Usage:
#   scripts/macos/install-launchd.sh             # LaunchAgents: run only while you are logged in
#   sudo scripts/macos/install-launchd.sh --daemon   # LaunchDaemons: run whether or not anyone is logged in
#   scripts/macos/install-launchd.sh --uninstall [--daemon]
#
# launchd uses the Mac's local clock. The ET times are converted to local time at install, which stays
# right across DST in any US time zone that observes DST. Re-run after moving the Mac to another zone.
# A sleeping Mac runs missed jobs only when it next wakes, so keep it awake on power:
#   sudo pmset -c sleep 0          (the display can still sleep)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MODE=agent; ACTION=install
for a in "$@"; do
  case "$a" in
    --daemon) MODE=daemon ;;
    --uninstall) ACTION=uninstall ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

if [[ $MODE == daemon ]]; then
  [[ $EUID -eq 0 ]] || { echo "--daemon needs sudo" >&2; exit 1; }
  RUN_USER="${SUDO_USER:?run with sudo from your own account}"
  DIR=/Library/LaunchDaemons; DOMAIN=system
else
  [[ $EUID -ne 0 ]] || { echo "run without sudo (or pass --daemon)" >&2; exit 1; }
  RUN_USER="$(id -un)"
  DIR="$HOME/Library/LaunchAgents"; DOMAIN="gui/$(id -u)"
fi

as_user() { if [[ $MODE == daemon ]]; then sudo -u "$RUN_USER" "$@"; else "$@"; fi; }

for job in forward gateway; do
  launchctl bootout "$DOMAIN/local.trader.$job" 2>/dev/null || true
  rm -f "$DIR/local.trader.$job.plist"
done
if [[ $ACTION == uninstall ]]; then echo "Removed local.trader.forward and local.trader.gateway ($MODE)."; exit 0; fi

USER_HOME="$(dscl . -read "/Users/$RUN_USER" NFSHomeDirectory | awk '{print $2}')"
UV="$(as_user bash -lc 'command -v uv' 2>/dev/null || true)"
[[ -n $UV ]] || UV="$USER_HOME/.local/bin/uv"
[[ -x $UV ]] || { echo "uv not found (install it: https://docs.astral.sh/uv/)" >&2; exit 1; }

# HH MM in local time for a New York wall-clock time today; warns if DST rules differ from New York's.
local_hm() {
  as_user "$UV" run --no-project python - "$1" <<'PY'
import sys
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
ny = ZoneInfo("America/New_York")
h, m = map(int, sys.argv[1].split(":"))
def off(d):
    t = datetime.combine(d, time(h, m), ny)
    return t.astimezone().utcoffset() - t.utcoffset()
if off(date(2026, 1, 15)) != off(date(2026, 7, 15)):
    print("warning: this time zone's DST rules differ from New York's; re-run after each DST change", file=sys.stderr)
print(datetime.combine(date.today(), time(h, m), ny).astimezone().strftime("%H %M"))
PY
}
read -r FWD_H FWD_M < <(local_hm 19:00)
read -r GW_H GW_M < <(local_hm 09:40)

intervals() {  # $1 hour, $2 minute, $3 weekdays (launchd: 0=Sunday)
  for d in $3; do
    printf '    <dict><key>Weekday</key><integer>%s</integer><key>Hour</key><integer>%s</integer><key>Minute</key><integer>%s</integer></dict>\n' "$d" "$((10#$1))" "$((10#$2))"
  done
}

write_plist() {  # $1 job, $2 intervals
  local path="$DIR/local.trader.$1.plist" user_key=""
  if [[ $MODE == daemon ]]; then user_key="  <key>UserName</key><string>$RUN_USER</string>"; fi
  cat >"$path" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.trader.$1</string>
$user_key
  <key>ProgramArguments</key>
  <array><string>$UV</string><string>run</string><string>--no-sync</string><string>trader</string><string>job</string><string>$1</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$(dirname "$UV"):/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    <key>HOME</key><string>$USER_HOME</string>
  </dict>
  <key>StartCalendarInterval</key>
  <array>
$2
  </array>
  <key>StandardOutPath</key><string>$ROOT/data/logs/scheduler.log</string>
  <key>StandardErrorPath</key><string>$ROOT/data/logs/scheduler.log</string>
</dict>
</plist>
EOF
  plutil -lint "$path" >/dev/null
  if [[ $MODE == daemon ]]; then chown root:wheel "$path"; chmod 644 "$path"; fi
  launchctl bootstrap "$DOMAIN" "$path"
}

as_user mkdir -p "$ROOT/data/logs"
mkdir -p "$DIR"
write_plist forward "$(intervals "$FWD_H" "$FWD_M" "0 1 2 3 4 5 6")"
write_plist gateway "$(intervals "$GW_H" "$GW_M" "1 2 3 4 5")"
echo "Installed ($MODE): forward daily at $FWD_H:$FWD_M, gateway Mon-Fri at $GW_H:$GW_M (local time)."
echo "Check:  launchctl print $DOMAIN/local.trader.gateway | grep -E 'state|last exit'"
echo "Logs:   $ROOT/data/logs/"
