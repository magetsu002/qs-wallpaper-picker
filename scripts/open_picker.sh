#!/usr/bin/env bash

set -u

SCRIPT_DIR="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &&
    pwd
)"
PROJECT_DIR="$(
    cd -- "$SCRIPT_DIR/.." &&
    pwd
)"

WALLPAPER_DIR="${QS_WALLPAPER_DIR:-$HOME/Wallpapers}"
STATE_DIR="$HOME/.cache/wallpaper_picker"

mkdir -p "$STATE_DIR"

"$SCRIPT_DIR/sync_thumbs.sh" "$WALLPAPER_DIR"

if command -v flock >/dev/null 2>&1; then
    exec 9>"$STATE_DIR/picker.lock"

    if ! flock -n 9; then
        exit 0
    fi
fi

exec quickshell -p "$PROJECT_DIR/Main.qml"
