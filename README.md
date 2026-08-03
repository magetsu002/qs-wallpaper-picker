# QS Wallpaper Picker

A fast keyboard-first Quickshell wallpaper picker with local image and video support, color filters, animated previews and quality-ranked Wallhaven search.

<!-- Add the final picker screenshot here after capture. Suggested path: docs/assets/wallpaper-picker-preview.png -->

## Primary features

- Keyboard-first wallpaper browsing
- Local image wallpapers
- Local video wallpapers
- Animated image and video previews
- Color-based filtering
- Explicit local and online search
- Display-aware ranked Wallhaven results
- Preview-first safe full-resolution downloads

## Quick installation

```bash
git clone https://github.com/magetsu002/qs-wallpaper-picker.git
cd qs-wallpaper-picker
cp config/Settings.qml.example config/Settings.qml
mkdir -p "$HOME/Wallpapers"
./scripts/open_picker.sh
```

The tracked template is [`config/Settings.qml.example`](config/Settings.qml.example). Edit your copied, ignored `config/Settings.qml` for local preferences.

## Launching

Use the launcher for normal operation:

```bash
./scripts/open_picker.sh
```

It resolves the project path, synchronizes thumbnails, initializes the XDG-aware cache contract, prevents duplicate picker instances and launches `Main.qml`.

Direct launch is an advanced alternative after setup:

```bash
quickshell -p Main.qml
```

## Hyprland keybind

Use the absolute repository path so the launcher can resolve every supporting file:

```ini
bind = SUPER, W, exec, /absolute/path/to/qs-wallpaper-picker/scripts/open_picker.sh
```

## Controls

- **Left / Right** — move between wallpapers
- **Enter** — apply the selected wallpaper
- **Tab / Shift+Tab** — move between filters
- **Typing in Search** — filter local filenames
- **Enter in Search** — search Wallhaven
- **Escape in Search** — return to All
- **Escape elsewhere** — close the picker
- **Mouse click** — select and apply

## Local versus online search

Typing searches local wallpaper filenames.

Pressing Enter searches Wallhaven even when local matches exist.

Online search downloads validated previews first. The full-resolution image is downloaded only after selection, through the validated production downloader. A failed download does not apply a partial file or close the picker as though it succeeded.

## Requirements

### Required for normal image usage

- Linux
- Hyprland
- [Quickshell](https://quickshell.org/)
- Bash
- Python 3.12, the version certified by CI
- `awww` for image wallpaper application and transitions
- ImageMagick (`magick`) for local image thumbnails and color extraction

### Required for video support

- `ffmpeg` for video thumbnail generation
- `mpvpaper` for video wallpaper playback

### Optional integrations

- Matugen for dynamic colors
- ML4W synchronization
- Waybar, Kitty, Cava, SwayNC and SwayOSD reload targets
- `hyprctl`, `wlr-randr` or `xrandr` for display detection; a safe fallback exists

### Development and testing

- Python standard library
- Bash
- Git

Wallhaven search requires no account, API key, cloud AI service, GPU model or third-party Python package.

## Basic configuration

Create the local settings file once:

```bash
cp config/Settings.qml.example config/Settings.qml
```

Useful environment overrides before launching include:

```bash
export QS_WALLPAPER_DIR="$HOME/Pictures/Wallpapers"
export QS_WALLPAPER_RESULT_LIMIT=12
export QS_WALLPAPER_CANDIDATE_LIMIT=72
export QS_WALLPAPER_SEARCH_JOBS=6
./scripts/open_picker.sh
```

The wallpaper directory falls back to `$HOME/Wallpapers`. `XDG_CACHE_HOME` is honored when set; otherwise cache state uses `$HOME/.cache/wallpaper_picker`.

Optional desktop integrations are disabled in the public settings template. Enable only the integrations you use by changing the corresponding `enable...` properties in your copied `config/Settings.qml`. ML4W synchronization is separately opt-in with `QS_WALLPAPER_ENABLE_ML4W=1`.

## Troubleshooting

### No local wallpapers appear

Confirm files exist in `QS_WALLPAPER_DIR` or the `wallpaperDir` configured in your copied settings file, then launch with `./scripts/open_picker.sh`.

### Image thumbnails do not appear

Install ImageMagick and confirm `magick` is available. The launcher regenerates missing or outdated thumbnails.

### Video thumbnails do not appear

Install `ffmpeg`. Video playback additionally requires `mpvpaper`.

### Online search returns no results

Try a broader query. Candidates can also be rejected for display dimensions, orientation, aspect ratio, metadata or preview validation.

### Online search times out

Check network access to Wallhaven and increase the validated timeout variables only when necessary. See the advanced reference.

### Download failed

Retry the selection after confirming the destination wallpaper directory is writable. The previous file and online cache remain protected.

### Colors reload unexpectedly

Keep the optional integration flags disabled or check for external color-generation watchers and reload scripts.

> Avoid running multiple automatic color generators simultaneously because competing watchers may overwrite Hyprland or Waybar color files.

## Advanced documentation

See the [advanced online-discovery reference](docs/online-discovery.md) for ranking, cache safety, configuration and testing details.

## Privacy

- Online search sends the normalized query and display constraints to Wallhaven.
- Preview images download during explicit online search.
- Full-resolution images download only after selection.
- Local wallpaper filenames and personal account data are not transmitted.
- No cloud AI service or online account is required.

## Credits

Created and maintained by **Magetsu**.

Original UI design adapted from [ilyamiro's NixOS configuration](https://github.com/ilyamiro/nixos-configuration).

Original online-search contribution by **bay0n**. The quality-ranking engine builds on that contribution, and existing Git co-author attribution remains preserved.

## License

See [LICENSE](LICENSE).
