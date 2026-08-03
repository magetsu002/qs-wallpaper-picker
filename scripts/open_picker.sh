#!/usr/bin/env bash

~/.config/quickshell/wallpaper/scripts/sync_thumbs.sh

pkill quickshell 2>/dev/null || true

quickshell -p ~/.config/quickshell/wallpaper/Main.qml
