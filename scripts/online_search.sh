#!/usr/bin/env bash

set -u

QUERY="${1:-}"
CACHE_DIR="$HOME/.cache/wallpaper_picker"
THUMB_DIR="$CACHE_DIR/search_thumbs"
MAP_FILE="$CACHE_DIR/search_map.txt"
JOBS="${QS_WALLPAPER_SEARCH_JOBS:-6}"
LIMIT="${QS_WALLPAPER_SEARCH_LIMIT:-24}"

if ! [[ "$JOBS" =~ ^[1-9][0-9]*$ ]]; then
    echo "QS_WALLPAPER_SEARCH_JOBS must be a positive integer." >&2
    exit 2
fi

if ! [[ "$LIMIT" =~ ^[1-9][0-9]*$ ]] ||
   (( LIMIT > 24 ))
then
    echo "QS_WALLPAPER_SEARCH_LIMIT must be between 1 and 24." >&2
    exit 2
fi

if [[ -z "${QUERY//[[:space:]]/}" ]]; then
    echo "Search query is empty." >&2
    exit 2
fi

for dependency in curl python3; do
    if ! command -v "$dependency" >/dev/null 2>&1; then
        echo "Missing dependency: $dependency" >&2
        exit 3
    fi
done

mkdir -p "$CACHE_DIR" "$THUMB_DIR"

TMP_DIR="$(mktemp -d "$CACHE_DIR/search.XXXXXX")" || exit 4
trap 'rm -rf "$TMP_DIR"' EXIT

RESPONSE="$TMP_DIR/response.json"
RESULTS="$TMP_DIR/results.tsv"
NEW_THUMBS="$TMP_DIR/thumbs"
MAP_PARTS="$TMP_DIR/maps"
NEW_MAP="$TMP_DIR/search_map.txt"

mkdir -p "$NEW_THUMBS" "$MAP_PARTS"

ENCODED="$(
    python3 - "$QUERY" <<'PYURL'
import sys
import urllib.parse
print(urllib.parse.quote(sys.argv[1]))
PYURL
)"

curl \
    --fail \
    --silent \
    --show-error \
    --location \
    --retry 2 \
    --connect-timeout 8 \
    --max-time 30 \
    --user-agent "qs-wallpaper-picker/2.0" \
    "https://wallhaven.cc/api/v1/search?q=${ENCODED}&purity=100&sorting=relevance&per_page=${LIMIT}" \
    --output "$RESPONSE" ||
{
    echo "Wallhaven request failed." >&2
    exit 5
}

python3 - "$RESPONSE" "$LIMIT" >"$RESULTS" <<'PYPARSE'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)

limit = int(sys.argv[2])

for item in payload.get("data", [])[:limit]:
    wallpaper_id = str(item.get("id", "")).strip()
    full_url = str(item.get("path", "")).strip()
    thumbs = item.get("thumbs") or {}
    preview_url = str(
        thumbs.get("large")
        or thumbs.get("original")
        or thumbs.get("small")
        or ""
    ).strip()

    if wallpaper_id and full_url and preview_url:
        print(wallpaper_id, full_url, preview_url, sep="\t")
PYPARSE

active=0

while IFS=$'\t' read -r wallpaper_id full_url preview_url; do
    [[ -n "$wallpaper_id" ]] || continue

    extension="${full_url%%\?*}"
    extension="${extension##*.}"
    extension="${extension,,}"

    case "$extension" in
        jpg|jpeg|png|webp) ;;
        *) extension="jpg" ;;
    esac

    file_name="wallhaven-${wallpaper_id}.${extension}"

    (
        temp_file="$NEW_THUMBS/${file_name}.tmp"

        if curl \
            --fail \
            --silent \
            --show-error \
            --location \
            --retry 1 \
            --connect-timeout 8 \
            --max-time 25 \
            --user-agent "qs-wallpaper-picker/2.0" \
            "$preview_url" \
            --output "$temp_file"
        then
            mv -f "$temp_file" "$NEW_THUMBS/$file_name"
            printf '%s|%s\n' \
                "$file_name" \
                "$full_url" \
                >"$MAP_PARTS/$wallpaper_id"
        else
            rm -f "$temp_file"
        fi
    ) &

    active=$((active + 1))

    if (( active >= JOBS )); then
        wait -n || true
        active=$((active - 1))
    fi
done <"$RESULTS"

wait || true

: >"$NEW_MAP"

mapfile -d '' parts < <(
    find "$MAP_PARTS" \
        -maxdepth 1 \
        -type f \
        -print0 |
    sort -z
)

if (( ${#parts[@]} > 0 )); then
    cat "${parts[@]}" >>"$NEW_MAP"
fi

find "$THUMB_DIR" \
    -mindepth 1 \
    -maxdepth 1 \
    -type f \
    -delete

find "$NEW_THUMBS" \
    -maxdepth 1 \
    -type f \
    -exec cp -f {} "$THUMB_DIR/" \;

mv -f "$NEW_MAP" "$MAP_FILE"
cat "$MAP_FILE"
