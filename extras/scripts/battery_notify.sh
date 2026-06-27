#!/usr/bin/env bash
# Battery notification daemon — polls every 60s, notifies at threshold crossings.
# Uses same thresholds and cooldown approach as the sway status_bar_info.sh script.

set -euo pipefail

BATTERY_LOW=15
BATTERY_CRITICAL=5
COOLDOWN=300
STATE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/driftwm-battery-notify.XXXXXX")"
mkdir -p "$STATE_DIR"

notify() {
    command -v notify-send >/dev/null 2>&1 && notify-send "$@" || true
}

check_cooldown() {
    local key="$1"
    local now
    now=$(date +%s)
    local state_file="$STATE_DIR/$key"
    if [ -f "$state_file" ]; then
        local last
        last=$(cat "$state_file")
        [ $((now - last)) -lt "$COOLDOWN" ] && return 1
    fi
    echo "$now" > "$state_file"
    return 0
}

trap 'rm -rf "$STATE_DIR"; exit 0' EXIT INT TERM

while true; do
    bat=$(cat /sys/class/power_supply/BAT*/capacity 2>/dev/null | head -n 1 || true)
    status=$(cat /sys/class/power_supply/BAT*/status 2>/dev/null | head -n 1 || true)

    case "$bat" in
        ''|*[!0-9]*)
            sleep 60
            continue
            ;;
    esac

    if [ "$status" = "Discharging" ]; then
        if [ "$bat" -le "$BATTERY_CRITICAL" ]; then
            check_cooldown critical && \
                notify -u critical "Critical Battery" "${bat}% - plug in immediately"
        elif [ "$bat" -le "$BATTERY_LOW" ]; then
            check_cooldown low && \
                notify -u normal "Low Battery" "${bat}% - consider charging soon"
        fi
    fi

    sleep 60
done
