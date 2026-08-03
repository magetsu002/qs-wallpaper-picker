#!/usr/bin/env bash

set -u

wallpaper_cache_home() {
    printf '%s\n' "${XDG_CACHE_HOME:-$HOME/.cache}"
}

wallpaper_cache_dir() {
    printf '%s/wallpaper_picker\n' "$(wallpaper_cache_home)"
}

legacy_wallpaper_cache_dir() {
    printf '%s/.cache/wallpaper_picker\n' "$HOME"
}

ensure_wallpaper_cache_compatibility() {
    local cache_dir legacy_dir backup_dir
    cache_dir="$(wallpaper_cache_dir)"
    legacy_dir="$(legacy_wallpaper_cache_dir)"

    mkdir -p "$cache_dir"

    if [[ "$cache_dir" == "$legacy_dir" ]]; then
        return 0
    fi

    mkdir -p "$(dirname -- "$legacy_dir")"

    if [[ -L "$legacy_dir" ]]; then
        if [[ "$(readlink -f -- "$legacy_dir")" == "$(readlink -f -- "$cache_dir")" ]]; then
            return 0
        fi
        rm -f -- "$legacy_dir"
    elif [[ -d "$legacy_dir" ]]; then
        cp -a -n -- "$legacy_dir/." "$cache_dir/"
        backup_dir="${legacy_dir}.pre-xdg-$(date +%s)"
        mv -- "$legacy_dir" "$backup_dir"
        printf 'Migrated legacy wallpaper-picker cache to %s; backup: %s\n' \
            "$cache_dir" "$backup_dir" >&2
    elif [[ -e "$legacy_dir" ]]; then
        printf 'Cannot create cache compatibility link: %s is not a directory or symlink.\n' \
            "$legacy_dir" >&2
        return 2
    fi

    ln -s -- "$cache_dir" "$legacy_dir"
}
