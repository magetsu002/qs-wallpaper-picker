# QS Wallpaper Picker

A fast keyboard-first Quickshell wallpaper picker with local image and video support, color filters, animated previews and high-quality Wallhaven discovery.

<!-- Add the final picker screenshot here after capture. Suggested path: docs/assets/wallpaper-picker-preview.png -->

## Features

- Keyboard-first wallpaper browsing
- Local image and video wallpapers
- Animated image and video previews
- Color-based filtering
- Explicit local and online search
- Display-aware, quality-ranked Wallhaven results
- Preview-first browsing with safe full-resolution downloads

## Installation

Clone the repository and enter it:

```bash
git clone https://github.com/magetsu002/qs-wallpaper-picker.git
cd qs-wallpaper-picker
```

The default wallpaper directory is `$HOME/Wallpapers`. Create it when needed:

```bash
mkdir -p "$HOME/Wallpapers"
```

Settings such as the wallpaper directory, transitions and desktop integrations are available in [`config/Settings.qml`](config/Settings.qml).

## Launching

Generate or refresh local previews, then launch the picker from the repository directory:

```bash
bash scripts/sync_thumbs.sh "$HOME/Wallpapers"
quickshell -p Main.qml
```

## Controls

- **Left / Right** — move between wallpapers
- **Enter** — apply the selected wallpaper
- **Tab / Shift+Tab** — move between filters
- **Search field** — filter local wallpaper filenames while typing
- **Enter in Search** — search Wallhaven explicitly
- **Escape** — leave the Search view
- **Mouse click** — select and apply a wallpaper

## Local and online search

Typing filters local wallpaper filenames.

Pressing Enter searches Wallhaven, even when local matches exist.

The picker labels local results, online searching, online results, empty results and download failures separately. Online results are filtered and ranked for the active display instead of being shown in API order.

Preview images download during search. Full-resolution files download only after the user selects an online result. Selection uses the validated production downloader and does not trust arbitrary URLs inside QML.

## Requirements

### Required

- Linux with Hyprland
- [Quickshell](https://quickshell.org/)
- Python 3.11 or newer
- Bash
- `awww` for image wallpapers and transitions
- `mpvpaper` when using video wallpapers

### Optional

- ImageMagick for enhanced thumbnails and color extraction
- Matugen for dynamic colors
- ML4W integration
- `hyprctl`, `wlr-randr` or `xrandr` for display detection; otherwise a safe fallback is used

### Development and testing

- Python standard library
- Bash
- Git

No Wallhaven account, API key, cloud AI service, GPU model or third-party Python package is required.

## Configuration

Most users only need [`config/Settings.qml`](config/Settings.qml). Useful environment overrides include:

```bash
QS_WALLPAPER_DIR="$HOME/Wallpapers"
QS_WALLPAPER_TARGET_WIDTH=2560
QS_WALLPAPER_TARGET_HEIGHT=1440
QS_WALLPAPER_RESULT_LIMIT=12
QS_WALLPAPER_SEARCH_JOBS=6
```

The complete validated online-search reference covers `QS_WALLPAPER_TARGET_WIDTH`, `QS_WALLPAPER_TARGET_HEIGHT`, `QS_WALLPAPER_RESULT_LIMIT`, `QS_WALLPAPER_CANDIDATE_LIMIT`, `QS_WALLPAPER_SEARCH_JOBS`, `QS_WALLPAPER_MIN_WIDTH`, `QS_WALLPAPER_MIN_HEIGHT`, `QS_WALLPAPER_MAX_RATIO_ERROR`, `QS_WALLPAPER_CONNECT_TIMEOUT`, `QS_WALLPAPER_TOTAL_TIMEOUT` and `QS_WALLPAPER_RETRIES`.

## Advanced documentation

See [Advanced online-discovery details](docs/online-discovery.md) for retrieval strategies, quality filters, the deterministic score, preview validation, failure behavior, configuration, developer testing and the atomic publication pointer.

Metadata ranking improves measurable display fit and source quality. It does not claim to understand subjective artistic quality or perform AI aesthetic analysis.

## Privacy

- Online search terms and display constraints are sent to Wallhaven.
- Validated preview images download during an explicit online search.
- Full-resolution images download only after selection.
- Local wallpaper filenames and personal account data are not sent.
- No cloud AI service or online account is required.

## Credits

QS Wallpaper Picker was created by **Magetsu**.

The original online-search contribution was implemented by **bay0n**. Its existing co-author attribution remains preserved in Git history, and the quality engine builds on that work.

## License

See [LICENSE](LICENSE).
