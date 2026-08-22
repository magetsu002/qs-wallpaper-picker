#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &&
    pwd
)"
PROJECT_DIR="$(
    cd -- "$SCRIPT_DIR/.." &&
    pwd
)"

# shellcheck source=cache_paths.sh
source "$SCRIPT_DIR/cache_paths.sh"

WALLPAPER_DIR="${QS_WALLPAPER_DIR:-$HOME/Wallpapers}"
STATE_DIR="$(wallpaper_cache_dir)"

ensure_wallpaper_cache_compatibility
mkdir -p "$WALLPAPER_DIR" "$STATE_DIR"

# Optional ML4W synchronization is opt-in for a fresh clone.
export QS_WALLPAPER_ENABLE_ML4W="${QS_WALLPAPER_ENABLE_ML4W:-0}"

"$SCRIPT_DIR/sync_thumbs.sh" "$WALLPAPER_DIR"

if command -v flock >/dev/null 2>&1; then
    exec flock -n -o \
        "$STATE_DIR/picker.lock" \
        quickshell -p "$PROJECT_DIR/Main.qml"
fi

exec quickshell -p "$PROJECT_DIR/Main.qml"
