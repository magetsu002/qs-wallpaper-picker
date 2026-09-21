#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WALLPAPER_DIR="${1:-}"
SOURCE_NAME="${2:-}"

if [[ -z "$WALLPAPER_DIR" || -z "$SOURCE_NAME" || "$(basename -- "$SOURCE_NAME")" != "$SOURCE_NAME" ]]; then
    echo "invalid wallpaper target" >&2
    exit 2
fi

WALLPAPER_ROOT="$(realpath -e -- "$WALLPAPER_DIR")"
TARGET="$(realpath -e -- "$WALLPAPER_ROOT/$SOURCE_NAME")"

if [[ ! -f "$TARGET" || "$(dirname -- "$TARGET")" != "$WALLPAPER_ROOT" ]]; then
    echo "wallpaper target is outside the configured directory" >&2
    exit 2
fi

STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
STATE_FILE="$STATE_HOME/maho/wallpaper/current.json"
if [[ -f "$STATE_FILE" ]]; then
    ACTIVE="$(python3 - "$STATE_FILE" <<'PY'
import json
import os
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        value = json.load(handle).get("path", "")
except (OSError, ValueError, AttributeError):
    value = ""
print(os.path.realpath(value) if value else "")
PY
)"
    if [[ -n "$ACTIVE" && "$ACTIVE" == "$TARGET" ]]; then
        echo "refusing to trash the active wallpaper" >&2
        exit 3
    fi
fi

command -v gio >/dev/null 2>&1 || {
    echo "gio is required for recoverable deletion" >&2
    exit 4
}

gio trash -- "$TARGET"
"$SCRIPT_DIR/sync_thumbs.sh" "$WALLPAPER_ROOT"
