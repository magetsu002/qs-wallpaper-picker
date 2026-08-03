#!/usr/bin/env bash

LAST="$HOME/.cache/wallpaper_picker/last_wallpaper"
SRC="$HOME/Wallpapers"

[ -f "$LAST" ] || exit 0

type="$(cut -d'|' -f1 "$LAST")"
file="$(cut -d'|' -f2- "$LAST")"

pkill mpvpaper 2>/dev/null || true

if [ "$type" = "video" ]; then
    awww clear >/dev/null 2>&1 || true
    swww clear >/dev/null 2>&1 || true
    sleep 0.3
    mpvpaper -o 'loop --no-audio --hwdec=auto --profile=high-quality --video-sync=display-resample --interpolation --tscale=oversample --panscan=1.0 --video-unscaled=no' '*' "$SRC/$file" >/tmp/mpvpaper.log 2>&1 &
else
    pkill mpvpaper 2>/dev/null || true
    awww img --transition-type fade --transition-duration 0.4 "$SRC/$file" >/dev/null 2>&1 || true
fi
