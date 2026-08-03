#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &&
    pwd
)"
# shellcheck source=cache_paths.sh
source "$SCRIPT_DIR/cache_paths.sh"

SOURCE_DIR="${1:-${QS_WALLPAPER_DIR:-$HOME/Wallpapers}}"
THUMB_DIR="$(wallpaper_cache_dir)/thumbs"

if [[ ! -d "$SOURCE_DIR" ]]; then
    echo "Wallpaper directory does not exist: $SOURCE_DIR" >&2
    exit 2
fi

mkdir -p "$THUMB_DIR"

EXPECTED="$(mktemp)"
trap 'rm -f "$EXPECTED"' EXIT

while IFS= read -r -d '' source; do
    name="$(basename "$source")"
    extension="${name##*.}"
    extension="${extension,,}"

    case "$extension" in
        mp4|mkv|mov|webm)
            thumbnail_name="000_${name}.jpg"
            destination="$THUMB_DIR/$thumbnail_name"

            printf '%s\n' "$thumbnail_name" >>"$EXPECTED"

            if [[ -f "$destination" && "$destination" -nt "$source" ]]; then
                continue
            fi

            if command -v ffmpeg >/dev/null 2>&1; then
                temporary="${destination}.tmp.jpg"

                if ffmpeg \
                    -y \
                    -ss 00:00:01 \
                    -i "$source" \
                    -frames:v 1 \
                    -vf 'scale=-2:720' \
                    "$temporary" \
                    >/dev/null 2>&1
                then
                    mv -f "$temporary" "$destination"
                else
                    rm -f "$temporary"
                fi
            fi
            ;;

        jpg|jpeg|png|webp|gif)
            thumbnail_name="$name"
            destination="$THUMB_DIR/$thumbnail_name"
            temporary="${destination}.tmp.${extension}"

            printf '%s\n' "$thumbnail_name" >>"$EXPECTED"

            if [[ -f "$destination" && "$destination" -nt "$source" ]]; then
                continue
            fi

            if command -v magick >/dev/null 2>&1; then
                if magick \
                    "$source" \
                    -auto-orient \
                    -thumbnail '1280x720>' \
                    "$temporary"
                then
                    mv -f "$temporary" "$destination"
                else
                    rm -f "$temporary"
                fi
            fi
            ;;
    esac
done < <(
    find "$SOURCE_DIR" \
        -maxdepth 1 \
        -type f \
        -print0
)

while IFS= read -r -d '' thumbnail; do
    name="$(basename "$thumbnail")"

    if ! grep -Fqx -- "$name" "$EXPECTED"; then
        rm -f "$thumbnail"
    fi
done < <(
    find "$THUMB_DIR" \
        -maxdepth 1 \
        -type f \
        -print0
)
