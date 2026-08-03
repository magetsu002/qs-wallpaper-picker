#!/usr/bin/env bash
set -u

WALL="${1:-}"
LOG="/tmp/matugen-reload.log"

ENABLE_DYNAMIC_COLORS="${ENABLE_DYNAMIC_COLORS:-0}"
ENABLE_MATUGEN="${ENABLE_MATUGEN:-0}"
ENABLE_HYPR_RELOAD="${ENABLE_HYPR_RELOAD:-0}"
ENABLE_WAYBAR_RELOAD="${ENABLE_WAYBAR_RELOAD:-0}"
ENABLE_KITTY_RELOAD="${ENABLE_KITTY_RELOAD:-0}"
ENABLE_CAVA_RELOAD="${ENABLE_CAVA_RELOAD:-0}"
ENABLE_SWAYNC_RELOAD="${ENABLE_SWAYNC_RELOAD:-0}"
ENABLE_SWAYOSD_RELOAD="${ENABLE_SWAYOSD_RELOAD:-0}"

HYPR_COLORS_PATH="${HYPR_COLORS_PATH:-$HOME/.config/hypr/colors.conf}"
WAYBAR_COLORS_PATH="${WAYBAR_COLORS_PATH:-$HOME/.config/waybar/colors.css}"
WAYBAR_LAUNCH_PATH="${WAYBAR_LAUNCH_PATH:-$HOME/.config/waybar/launch.sh}"
KITTY_SIGNAL_PROCESS="${KITTY_SIGNAL_PROCESS:-.kitty-wrapped}"
EXTRA_RELOAD_COMMAND="${EXTRA_RELOAD_COMMAND:-}"

echo "===== $(date) RUN scripts/matugen_reload.sh =====" >> "$LOG"
echo "WALL=$WALL" >> "$LOG"
echo "FLAGS: dynamic=$ENABLE_DYNAMIC_COLORS matugen=$ENABLE_MATUGEN hypr=$ENABLE_HYPR_RELOAD waybar=$ENABLE_WAYBAR_RELOAD kitty=$ENABLE_KITTY_RELOAD cava=$ENABLE_CAVA_RELOAD swaync=$ENABLE_SWAYNC_RELOAD swayosd=$ENABLE_SWAYOSD_RELOAD" >> "$LOG"
echo "PATHS: hypr=$HYPR_COLORS_PATH waybar=$WAYBAR_COLORS_PATH launch=$WAYBAR_LAUNCH_PATH" >> "$LOG"

before_hypr="$(grep -m1 '^\$source_color' "$HYPR_COLORS_PATH" 2>/dev/null || echo 'hypr:missing')"
before_waybar="$(grep -m1 '@define-color source_color' "$WAYBAR_COLORS_PATH" 2>/dev/null || echo 'waybar:missing')"

echo "BEFORE HYPR:   $before_hypr" >> "$LOG"
echo "BEFORE WAYBAR: $before_waybar" >> "$LOG"

if [ "$ENABLE_DYNAMIC_COLORS" = "1" ] && [ "$ENABLE_MATUGEN" = "1" ] && command -v matugen >/dev/null 2>&1 && [ -n "$WALL" ] && [ -f "$WALL" ]; then
    matugen image "$WALL" >/tmp/matugen-run.log 2>&1 || true
fi

if [ "$ENABLE_KITTY_RELOAD" = "1" ]; then
    killall -USR1 "$KITTY_SIGNAL_PROCESS" 2>/dev/null || true
fi

if [ "$ENABLE_CAVA_RELOAD" = "1" ] && pgrep -x cava >/dev/null; then
    cat ~/.config/cava/config_base ~/.config/cava/colors > ~/.config/cava/config 2>/dev/null || true
    killall -USR1 cava 2>/dev/null || true
fi

if [ "$ENABLE_SWAYNC_RELOAD" = "1" ] && command -v swaync-client >/dev/null 2>&1; then
    swaync-client -rs >/dev/null 2>&1 || true
fi

if [ "$ENABLE_SWAYOSD_RELOAD" = "1" ] && systemctl --user is-active --quiet swayosd.service; then
    systemctl --user restart swayosd.service >/dev/null 2>&1 || true
fi

if [ "$ENABLE_HYPR_RELOAD" = "1" ] && command -v hyprctl >/dev/null 2>&1; then
    hyprctl reload >/dev/null 2>&1 || true
    sleep 0.2
    hyprctl reload >/dev/null 2>&1 || true
fi

if [ "$ENABLE_WAYBAR_RELOAD" = "1" ] && [ -x "$WAYBAR_LAUNCH_PATH" ]; then
    pkill -9 waybar 2>/dev/null || true
    pkill -9 cava 2>/dev/null || true
    pkill -9 -f 'cava_waybar.sh' 2>/dev/null || true
    rm -f /tmp/waybar-launch.lock
    sleep 0.5
    nohup bash "$WAYBAR_LAUNCH_PATH" >/tmp/waybar-launch.log 2>&1 &
fi

if [ -n "$EXTRA_RELOAD_COMMAND" ]; then
    bash -lc "$EXTRA_RELOAD_COMMAND" >/tmp/wallpaper-picker-extra-reload.log 2>&1 || true
fi

after_hypr="$(grep -m1 '^\$source_color' "$HYPR_COLORS_PATH" 2>/dev/null || echo 'hypr:missing')"
after_waybar="$(grep -m1 '@define-color source_color' "$WAYBAR_COLORS_PATH" 2>/dev/null || echo 'waybar:missing')"

echo "AFTER HYPR:    $after_hypr" >> "$LOG"
echo "AFTER WAYBAR:  $after_waybar" >> "$LOG"
echo >> "$LOG"
