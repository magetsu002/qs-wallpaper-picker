#!/usr/bin/env bash

set -u

SRC="/home/magetsu/Wallpapers"
THUMBS="/home/magetsu/.cache/wallpaper_picker/thumbs"

mkdir -p "$THUMBS"

find "$THUMBS" -maxdepth 1 -type f | while read -r thumb; do
    base="$(basename "$thumb")"

    if [[ "$base" == 000_*.jpg ]]; then
        name="${base#000_}"
        name="${name%.jpg}"

        found=0
        for ext in mp4 mkv mov webm; do
            if [ -f "$SRC/$name.$ext" ]; then
                found=1
                break
            fi
        done

        [ "$found" -eq 1 ] || rm -f "$thumb"
    else
        [ -f "$SRC/$base" ] || rm -f "$thumb"
    fi
done

find "$SRC" -maxdepth 1 -type f \
  \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' -o -iname '*.gif' -o -iname '*.mp4' -o -iname '*.mkv' -o -iname '*.mov' -o -iname '*.webm' \) |
while read -r file; do
    base="$(basename "$file")"
    lower="${base,,}"

    case "$lower" in
        *.mp4|*.mkv|*.mov|*.webm)
            name="${base%.*}"
            thumb="$THUMBS/000_${name}.jpg"

            if [ ! -f "$thumb" ] || [ "$file" -nt "$thumb" ]; then
                ffmpeg -y -ss 1 -i "$file" \
                    -frames:v 1 -update 1 -vf "scale=-2:420" \
                    "$thumb" >/dev/null 2>&1
            fi
            ;;

        *.jpg|*.jpeg|*.png|*.webp|*.gif)
            thumb="$THUMBS/$base"

            if [ ! -f "$thumb" ] || [ "$file" -nt "$thumb" ]; then
                magick "$file" -resize x420 -quality 70 "$thumb"
            fi
            ;;
    esac
done
