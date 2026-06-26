#!/usr/bin/env bash
# Launch every NSO widget as its own native Wayland toplevel.
# driftwm turns these into canvas widgets through extras/nso/config/driftwm.toml.

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
WIDGET_BIN="${NSO_WIDGET_BIN:-}"

launch() {
    local widget="$1"
    if [[ -n "$WIDGET_BIN" ]]; then
        "$WIDGET_BIN" --widget "$widget" &
    else
        python3 "$DIR/nso_widget.py" --widget "$widget" &
    fi
}

launch welcome
launch task-manager
launch ame
launch jine
launch social-media
launch media-player
launch calendar
launch desktop-icons
launch quick-notes
launch medications
launch trash-bin

wait
