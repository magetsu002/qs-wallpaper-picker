# Online Discovery Reference

This reference documents the production Wallhaven pipeline, shared path contracts, failure guarantees and developer checks. Everyday setup and controls stay in the main [README](../README.md).

## Table of contents

- [Architecture](#architecture)
- [Controls and search flow](#controls-and-search-flow)
- [Wallpaper and cache paths](#wallpaper-and-cache-paths)
- [Retrieval, filtering and ranking](#retrieval-filtering-and-ranking)
- [Preview and cache safety](#preview-and-cache-safety)
- [Selected full-resolution download](#selected-full-resolution-download)
- [Configuration reference](#configuration-reference)
- [Dependencies](#dependencies)
- [Networking and privacy](#networking-and-privacy)
- [Troubleshooting](#troubleshooting)
- [Testing](#testing)

## Architecture

```text
Main.qml
└── window shell and top-level presentation

WallpaperPicker.qml
├── local and online search UI
├── request invalidation and stale-result rejection
├── online selection state machine
├── safe downloader invocation
├── wallpaper application
└── desktop integrations

scripts/open_picker.sh
├── project and wallpaper-directory resolution
├── XDG cache initialization and compatibility
├── thumbnail synchronization
├── duplicate-instance lock
└── Quickshell launch

scripts/online_search.sh
└── stable shell entry point
    └── scripts/preview_pipeline.py
        ├── scripts/wallpaper_search.py
        ├── preview validation and backfilling
        ├── transactional generation publication
        └── selected full-resolution download
```

The online engine uses the Python standard library only. QML passes normalized user choices as separate process arguments; it does not embed a selected remote URL into shell source.

## Controls and search flow

- Typing in Search filters local wallpaper filenames immediately.
- Enter in Search explicitly requests Wallhaven results, even when local matches exist.
- Escape while Search is active returns to the All/local view.
- Escape outside Search closes the picker.
- Selecting an online preview starts the validated full-resolution downloader.

The interface distinguishes local results, online searching, online results, empty results, search failure, downloading and download failure.

Each online request claims a monotonically increasing ID. Editing the query invalidates older requests. Result consumption and cache publication both reject stale IDs, so correctness does not depend on successfully terminating an older process.

## Wallpaper and cache paths

### Wallpaper directory

The production directory contract is:

1. non-empty `QS_WALLPAPER_DIR`
2. otherwise the copied `config/Settings.qml` value
3. the public template falls back to `$HOME/Wallpapers`

`config/Settings.qml.example`, `scripts/open_picker.sh`, `scripts/sync_thumbs.sh`, `scripts/restore_wallpaper.sh` and `WallpaperPicker.qml` all follow this contract. The launcher is the primary supported entry point for custom paths.

### Wallpaper-picker cache

The authoritative cache root is:

```text
${XDG_CACHE_HOME:-$HOME/.cache}/wallpaper_picker
```

It owns local thumbnails, color markers, online generations, compatibility preview/map paths, restoration state and the picker lock. ML4W remains under its own third-party cache location.

When `XDG_CACHE_HOME` differs from `$HOME/.cache`, `scripts/open_picker.sh` initializes the authoritative root and creates a compatibility link at `$HOME/.cache/wallpaper_picker`. Existing legacy data is copied without clobbering the XDG destination, preserved in a timestamped backup, and then replaced by the link. This keeps older QML-owned path references and the Python publisher on the same underlying cache.

Important paths:

```text
<cache-root>/thumbs
<cache-root>/colors_markers
<cache-root>/last_wallpaper
<cache-root>/picker.lock
<cache-root>/online/
<cache-root>/search_thumbs
<cache-root>/search_map.txt
```

## Retrieval, filtering and ranking

Every search is SFW and uses three deterministic bounded strategies:

1. relevance
2. toplist over the supported one-month range
3. favorites

Defaults:

```text
Raw candidate budget:        72
Maximum per API request:     24
Displayed validated results: 12
Preview workers:              6
Retries:                      1
Connection timeout:           8 seconds
Total timeout:               30 seconds
```

Candidates are rejected for malformed required metadata, unsafe URLs, insufficient dimensions, incompatible orientation, aspect-ratio error above the configured limit, duplicate IDs or URLs, and unreasonable bytes-per-pixel values when reliable size metadata exists.

Only expected HTTPS Wallhaven API and media paths are accepted. Embedded credentials, unexpected ports, unrelated hosts, loopback/private targets and unsafe redirects are rejected.

### Deterministic score

| Component | Maximum |
| --- | ---: |
| Retrieval source quality and bounded cross-source agreement | 30 |
| Aspect-ratio and crop fit | 25 |
| Resolution surplus | 20 |
| Bounded favorites, views and efficiency | 15 |
| File-size sanity | 10 |

Popularity uses capped logarithmic normalization. Final tie-breaking is total score, ratio score, resolution score, retrieval-source priority and wallpaper ID. API order and worker completion order cannot change the result order.

Metadata ranking improves measurable display fit and source quality. It does not claim to understand subjective artistic quality or perform AI aesthetic analysis.

## Preview and cache safety

Ranked previews download in bounded concurrent waves. Empty, text/error, unsupported, malformed, truncated, tiny and oversized responses are rejected. A failed high-ranked preview is backfilled by the next valid ranked candidate without changing deterministic order.

Each request builds an immutable generation:

```text
<cache-root>/online/generations/<request-id>/
├── manifest.json
├── search_map.txt
└── previews/
```

An `fcntl` publication lock protects one atomic `online/current` pointer. Retrieval failure, all-preview failure, interruption, stale authority, manifest failure or publication failure leaves the previous successful generation active. Cleanup occurs only after successful publication and retains a bounded rollback set.

Compatibility paths expose the active generation to QML:

```text
<cache-root>/search_thumbs
<cache-root>/search_map.txt
```

## Selected full-resolution download

Full-resolution images download only after selection.

`WallpaperPicker.qml` invokes the production downloader with an argument array:

```text
bash scripts/online_search.sh --download <file-name> --destination <path>
```

The downloader resolves the exact active-map entry, validates the Wallhaven URL, downloads to a temporary file on the destination filesystem, validates JPEG/PNG/WebP bytes and dimensions, flushes the file and atomically replaces the destination.

Only exit code 0 continues into thumbnail refresh, lock-screen image update, `awww`, Matugen and optional ML4W behavior. On failure, QML clears its locks, displays `Download failed`, keeps the picker open and does not apply a missing or partial file. Already-downloaded results skip the network step and use the same application helper.

## Configuration reference

Invalid values fail clearly rather than being silently corrected.

| Variable | Default | Validation and effect |
| --- | --- | --- |
| `QS_WALLPAPER_DIR` | `$HOME/Wallpapers` | Authoritative local wallpaper directory when non-empty |
| `XDG_CACHE_HOME` | `$HOME/.cache` | Base for all wallpaper-picker-owned cache state |
| `QS_WALLPAPER_TARGET_WIDTH` | detected or `1920` | `1..16384`; must be paired with height |
| `QS_WALLPAPER_TARGET_HEIGHT` | detected or `1080` | `1..16384`; must be paired with width |
| `QS_WALLPAPER_RESULT_LIMIT` | `12` | `1..24`; not above candidate limit |
| `QS_WALLPAPER_CANDIDATE_LIMIT` | `72` | `3..72`; at least result limit |
| `QS_WALLPAPER_SEARCH_JOBS` | `6` | `1..16` preview workers |
| `QS_WALLPAPER_MIN_WIDTH` | target width | `1..16384` |
| `QS_WALLPAPER_MIN_HEIGHT` | target height | `1..16384` |
| `QS_WALLPAPER_MAX_RATIO_ERROR` | `0.20` | `0.01..0.75` fractional mismatch |
| `QS_WALLPAPER_CONNECT_TIMEOUT` | `8` | `1..30`; not above total timeout |
| `QS_WALLPAPER_TOTAL_TIMEOUT` | `30` | `2..120`; at least connection timeout |
| `QS_WALLPAPER_RETRIES` | `1` | `0..3` bounded retries |
| `QS_WALLPAPER_ENABLE_ML4W` | `0` | Set to `1` to opt into ML4W synchronization |

`QS_WALLPAPER_SEARCH_LIMIT` remains a legacy fallback when `QS_WALLPAPER_RESULT_LIMIT` is unset.

The public settings template disables Matugen and all reload targets. Enable integrations intentionally in the copied, ignored `config/Settings.qml`.

## Dependencies

### Required for normal image usage

- Linux with Hyprland
- Quickshell
- Bash
- Python 3.12, the version certified by CI
- `awww`
- ImageMagick (`magick`) for local image thumbnails and color extraction

### Required for video support

- `ffmpeg` for video thumbnail generation
- `mpvpaper` for video playback

### Optional

- Matugen
- ML4W
- Waybar, Kitty, Cava, SwayNC and SwayOSD reload targets
- `hyprctl`, `wlr-randr` or `xrandr`; display scoring has a safe fallback

### Development and testing

- Python standard library
- Bash
- Git

Required CI does not contact Wallhaven.

## Networking and privacy

An explicit online search sends the normalized query and configured display constraints to Wallhaven. Validated previews download during search; full-resolution images download only after selection.

Local wallpaper filenames and personal account data are not transmitted. No Wallhaven account, API key, cloud AI service or GPU model is required.

## Troubleshooting

### No local thumbnails

Confirm `QS_WALLPAPER_DIR` or `wallpaperDir` points to an existing directory, install ImageMagick, and launch through `./scripts/open_picker.sh`.

### Missing video previews

Install `ffmpeg`; install `mpvpaper` for playback. Regenerate through the primary launcher.

### Online search timeout

Confirm Wallhaven connectivity. Increase `QS_WALLPAPER_CONNECT_TIMEOUT` or `QS_WALLPAPER_TOTAL_TIMEOUT` only within their validated ranges.

### No ranked candidates

The candidate pool may have failed dimension, orientation, aspect-ratio, metadata, URL or size-sanity filters. Try a broader query or review configured limits.

### Preview validation rejection

HTML/error bodies, malformed images, tiny previews and oversized responses are intentionally excluded. Lower-ranked valid candidates backfill them automatically.

### Selected download failure

Confirm the wallpaper directory is writable and the active map still contains the selected result. A failed download does not replace an existing destination or damage the online cache.

### Stale cache generation

Start a new search. Request authority prevents an older generation from publishing over the current one.

### Reset only the online cache

This destructive command is narrowly scoped to wallpaper-picker online state and compatibility entries; local thumbnails and restoration state remain:

```bash
cache_root="${XDG_CACHE_HOME:-$HOME/.cache}/wallpaper_picker"
rm -rf -- "$cache_root/online"
rm -f -- "$cache_root/search_thumbs" "$cache_root/search_map.txt"
```

Launch the picker and search again to rebuild online state.

### Custom `XDG_CACHE_HOME`

Export it before running `./scripts/open_picker.sh`. The launcher initializes the XDG root and compatibility link before Quickshell starts.

### Custom `QS_WALLPAPER_DIR`

Export it before the launcher, or set `wallpaperDir` in the copied settings file. Use the same environment when running `scripts/restore_wallpaper.sh` outside the launcher.

## Testing

Network-independent certification runs:

```bash
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
while IFS= read -r script; do bash -n "$script"; done < <(git ls-files 'scripts/*.sh')
```

GitHub Actions additionally verifies exact-head checkout, executable bits, deterministic ranking, preview/cache safety, QML downloader wiring, fresh-clone documentation, tracked links, launcher behavior, wallpaper/cache path consistency, mutually exclusive Escape shortcuts, safe public defaults, dependency and architecture accuracy, attribution, personal-path and credential scans, picker-process termination regression checks and Git whitespace integrity.

## Credits

Created and maintained by **Magetsu**.

Original UI design adapted from [ilyamiro's NixOS configuration](https://github.com/ilyamiro/nixos-configuration).

The quality-ranking engine builds on **bay0n**'s original online-search contribution. Existing Git co-author attribution remains preserved.
