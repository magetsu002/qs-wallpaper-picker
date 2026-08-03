# QS Wallpaper Picker

A fast, keyboard-first wallpaper picker for Hyprland, built with Quickshell.

Browse local images and videos, filter by color, enjoy animated previews, and discover display-aware Wallhaven wallpapers without leaving the picker.

<p align="center">
  <img
    width="2560"
    height="1600"
    alt="QS Wallpaper Picker interface"
    src="https://github.com/user-attachments/assets/d14fce0d-4ef9-4cca-8c41-94e4ffd893bd"
  />
</p>

## Highlights

- Keyboard-first wallpaper browsing
- Local image and video support
- Smooth animated previews
- Color-based filtering
- Explicit local and online search
- Display-aware Wallhaven ranking
- Preview-first online browsing
- Validated full-resolution downloads

## Quick start

```bash
git clone https://github.com/magetsu002/qs-wallpaper-picker.git
cd qs-wallpaper-picker

cp config/Settings.qml.example config/Settings.qml
mkdir -p "$HOME/Wallpapers"

./scripts/open_picker.sh
```

Add your wallpapers to `$HOME/Wallpapers`, or configure another directory before launching.

The tracked configuration template is [`config/Settings.qml.example`](config/Settings.qml.example). Your copied `config/Settings.qml` contains local preferences and is ignored by Git.

## Hyprland keybind

Use the absolute path to the launcher:

```ini
bind = SUPER, W, exec, /absolute/path/to/qs-wallpaper-picker/scripts/open_picker.sh
```

The launcher resolves the project path, creates the wallpaper directory when needed, synchronizes thumbnails, initializes cache compatibility, prevents duplicate picker instances, and launches the correct QML entry point.

<details>
<summary>Manual launch</summary>

After configuration and thumbnail synchronization, the picker can also be launched directly:

```bash
quickshell -p Main.qml
```

The repository launcher is recommended for normal use.

</details>

## Controls

| Input | Action |
| --- | --- |
| **Left / Right** | Move between wallpapers |
| **Enter** | Apply the selected wallpaper |
| **Tab / Shift+Tab** | Move between filters |
| **Type in Search** | Filter local filenames |
| **Enter in Search** | Search Wallhaven |
| **Escape in Search** | Return to the All filter |
| **Escape elsewhere** | Close the picker |
| **Mouse click** | Select and apply a wallpaper |

## Local and online search

**Typing searches your local wallpaper filenames.**

**Pressing Enter searches Wallhaven, even when local matches exist.**

Online candidates are filtered and ranked using display dimensions, aspect-ratio fit, resolution, popularity signals, metadata validity, and preview validation.

Only lightweight previews are downloaded during search. The full-resolution image is downloaded after selection through the validated production downloader.

Failed downloads do not apply partial files, replace valid wallpapers, or close the picker as though the operation succeeded.

## Requirements

### Normal image usage

- Linux with Hyprland
- [Quickshell](https://quickshell.org/)
- Bash
- Python 3.12, as certified by CI
- `awww` for image wallpaper application and transitions
- ImageMagick (`magick`) for image thumbnails and color extraction

### Video support

- `ffmpeg` for video thumbnail generation
- `mpvpaper` for video wallpaper playback

### Optional integrations

- Matugen for dynamic colors
- ML4W synchronization
- Waybar, Kitty, Cava, SwayNC, and SwayOSD reload targets
- `hyprctl`, `wlr-randr`, or `xrandr` for display detection

A safe display-size fallback is used when no supported detection utility is available.

Wallhaven search requires no account, API key, cloud AI service, GPU model, or third-party Python package.

## Configuration

Most users only need their copied file:

```text
config/Settings.qml
```

Common environment overrides:

```bash
export QS_WALLPAPER_DIR="$HOME/Pictures/Wallpapers"
export QS_WALLPAPER_RESULT_LIMIT=12
export QS_WALLPAPER_CANDIDATE_LIMIT=72
export QS_WALLPAPER_SEARCH_JOBS=6

./scripts/open_picker.sh
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `QS_WALLPAPER_DIR` | `$HOME/Wallpapers` | Local wallpaper directory |
| `QS_WALLPAPER_RESULT_LIMIT` | `12` | Maximum displayed online results |
| `QS_WALLPAPER_CANDIDATE_LIMIT` | `72` | Maximum raw online candidate budget |
| `QS_WALLPAPER_SEARCH_JOBS` | `6` | Concurrent preview workers |
| `QS_WALLPAPER_ENABLE_ML4W` | disabled | Enables optional ML4W synchronization |

`XDG_CACHE_HOME` is honored when configured. Otherwise, picker state is stored under:

```text
$HOME/.cache/wallpaper_picker
```

Optional desktop integrations are disabled in the public settings template. Enable only the integrations installed on your system.

## Troubleshooting

<details>
<summary><strong>No local wallpapers appear</strong></summary>

Confirm your wallpaper files exist in `QS_WALLPAPER_DIR` or in the `wallpaperDir` configured inside your copied `config/Settings.qml`.

Then relaunch with:

```bash
./scripts/open_picker.sh
```

</details>

<details>
<summary><strong>Image thumbnails do not appear</strong></summary>

Install ImageMagick and confirm that `magick` is available:

```bash
magick -version
```

The launcher regenerates missing or outdated thumbnails.

</details>

<details>
<summary><strong>Video thumbnails do not appear</strong></summary>

Install `ffmpeg` for thumbnail generation.

Applying video wallpapers additionally requires `mpvpaper`.

</details>

<details>
<summary><strong>Online search returns no results</strong></summary>

Try a broader query.

Candidates may also be rejected because of insufficient resolution, portrait orientation, display-ratio mismatch, malformed metadata, unsafe URLs, or invalid previews.

</details>

<details>
<summary><strong>Online search times out</strong></summary>

Confirm that Wallhaven is reachable from your network.

Validated timeout and retry settings are documented in the advanced reference.

</details>

<details>
<summary><strong>An online wallpaper fails to download</strong></summary>

Confirm that the wallpaper directory exists and is writable, then retry the selection.

Failed downloads preserve the previous wallpaper and online result cache.

</details>

<details>
<summary><strong>Colors reload unexpectedly</strong></summary>

Keep unused integration flags disabled and check for external Matugen, pywal, or custom watcher processes.

Avoid running multiple automatic color generators simultaneously. Competing watchers may overwrite Hyprland or Waybar color files.

</details>

## Advanced documentation

See the [advanced online-discovery reference](docs/online-discovery.md) for:

- retrieval and ranking behavior
- quality filters
- cache and publication safety
- full configuration reference
- download validation
- networking and privacy
- testing and troubleshooting

## Privacy

- Online search sends the normalized query and display constraints to Wallhaven.
- Validated previews download during an explicit online search.
- Full-resolution images download only after selection.
- Local wallpaper filenames are not transmitted.
- Personal account data is not required or transmitted.
- No cloud AI service or online account is required.

## Credits

Created and maintained by **Magetsu**.

### Original interface

The carousel-style wallpaper picker interface was adapted from the wallpaper picker design in [ilyamiro’s NixOS configuration](https://github.com/ilyamiro/nixos-configuration).

This project turns that visual concept into a standalone Quickshell application for Arch Linux and Hyprland, with local image and video support, animated previews, color filtering, wallpaper restoration, desktop integrations, and online wallpaper discovery.

Additional contributions by **bay0n** and other repository contributors are preserved in the Git history and contributors list.

## License

Licensed under the MIT License. See [LICENSE](LICENSE).
