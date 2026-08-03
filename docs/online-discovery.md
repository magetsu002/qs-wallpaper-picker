# Online Discovery Reference

This document describes the production Wallhaven discovery pipeline, its validated configuration and its failure guarantees. The main [README](../README.md) remains focused on installation and everyday use.

## User interaction

The picker keeps local and online search explicit:

1. Typing filters local wallpaper filenames immediately.
2. Press Enter to search online with the current non-empty query.
3. Online search can run even when local matches exist.
4. Previews are shown before any full-resolution image is downloaded.
5. Selecting an online result downloads and validates the original image before applying it.

The interface distinguishes local results, online searching, online results, no local results, no online results, online-search failure and selected-download failure.

## Production components

```text
Main.qml
└── explicit search UI and stale-result rejection

WallpaperPicker.qml
├── online result selection state machine
├── Process argument-array invocation
├── success and failure handling
└── existing wallpaper application, Matugen and ML4W behavior

scripts/online_search.sh
└── stable shell entry point
    └── scripts/preview_pipeline.py
        ├── scripts/wallpaper_search.py
        ├── preview validation and backfilling
        ├── transactional cache publication
        └── selected full-resolution download
```

The Python implementation uses only the standard library.

## Query handling and display target

Queries are:

- trimmed
- internal whitespace-normalized
- rejected when empty
- bounded to 160 characters
- passed as process arguments rather than shell source
- URL encoded by the request builder

The target display dimensions come from, in order:

1. validated `QS_WALLPAPER_TARGET_WIDTH` and `QS_WALLPAPER_TARGET_HEIGHT` overrides
2. the focused Hyprland monitor reported by `hyprctl`
3. another supported display utility where available
4. the safe `1920x1080` fallback

Width and height overrides must be supplied together.

## Request authority and stale results

Each online search claims a monotonically increasing request ID. Editing the query invalidates older IDs.

Correctness does not depend on successfully terminating an older process. Both result consumption and cache publication verify authority:

- stale QML output is ignored
- stale preview waves stop before further work
- stale generations cannot replace the active cache
- publication checks authority again while holding the publication lock

## Bounded retrieval

Every search is SFW and uses three deterministic Wallhaven strategies:

1. relevance
2. toplist with the supported one-month range
3. favorites

Defaults:

```text
Raw candidate budget:       72
Maximum per API request:    24
Displayed validated results: 12
Preview workers:             6
Retries:                     1
Connection timeout:          8 seconds
Total timeout:              30 seconds
```

The implementation does not use random ordering, uncontrolled pagination or a required API key.

## URL security

Only expected HTTPS Wallhaven endpoints are accepted.

The validator rejects URLs with:

- a non-HTTPS scheme
- embedded credentials
- unexpected ports
- unrelated hosts
- loopback, link-local or private-network targets
- unsupported API, preview or full-image paths
- unsafe redirect destinations

Redirects are bounded and the final URL is validated again.

QML never receives or interpolates an arbitrary full-resolution URL. It passes only the selected result filename and destination path to the production downloader:

```text
scripts/online_search.sh --download <file-name> --destination <path>
```

## Hard candidate rejection

Candidates are rejected for:

- missing or malformed wallpaper IDs
- missing or unsafe full-resolution URLs
- missing or unsafe preview URLs
- missing, zero or negative dimensions
- dimensions below the configured minimum
- orientation incompatible with the display target
- aspect-ratio error beyond the configured limit
- duplicate wallpaper IDs
- duplicate full-resolution URLs
- duplicate preview URLs representing the same asset
- malformed required metadata
- clearly unreasonable bytes-per-pixel characteristics when reliable file-size metadata exists

Favorites, views and file size are optional. Missing optional popularity metadata receives a conservative score rather than causing rejection.

## Deterministic quality score

Every accepted candidate receives a score out of 100.

| Component | Maximum | Purpose |
| --- | ---: | --- |
| Retrieval source quality | 30 | Rewards useful retrieval sources and bounded cross-source agreement. |
| Aspect-ratio and crop fit | 25 | Rewards exact display-ratio matches and penalizes crop loss. |
| Resolution surplus | 20 | Rewards useful resolution above the target without unbounded growth. |
| Favorites and view efficiency | 15 | Uses bounded logarithmic normalization and a capped efficiency term. |
| File-size sanity | 10 | Rewards plausible bytes-per-pixel values without favoring arbitrarily large files. |

Final ordering is deterministic:

1. total score descending
2. ratio score descending
3. resolution score descending
4. best retrieval-source priority
5. wallpaper ID ascending

API response order, network completion order and preview completion order cannot decide the displayed order.

## Popularity normalization

Favorites and views use bounded `log1p` normalization. The values are capped within their score component, so a viral wallpaper cannot dominate display fit, resolution and source quality.

A favorite-to-view efficiency term is also bounded. Missing popularity metadata receives a small conservative default rather than zeroing the whole candidate or inventing engagement.

## Aspect-ratio and resolution behavior

A candidate must meet the configured minimum dimensions and orientation before scoring.

Aspect-ratio error is measured against the target display. Exact matches receive the maximum ratio score. The score falls continuously toward zero at `QS_WALLPAPER_MAX_RATIO_ERROR`; candidates beyond that boundary are rejected.

Resolution credit is based on useful surplus above the target dimensions and is capped. Extremely oversized images do not receive unlimited points.

## Preview validation and backfilling

Previews download only after deterministic ranking. Workers process bounded ranked waves until the result limit is filled or the ranked pool is exhausted.

Validation rejects:

- empty bodies
- zero-byte files
- HTML, JSON, XML and obvious text error responses
- unsupported signatures
- malformed or truncated JPEG, PNG and WebP structures
- invalid dimensions
- previews below the minimum useful size
- responses above the preview byte limit

`Content-Type` is considered but is never trusted by itself.

When a high-ranked preview fails, the next valid ranked candidate backfills its place. Successful worker completion order does not reorder results.

## Transactional cache

The online cache uses immutable generations and one atomic publication pointer:

```text
${XDG_CACHE_HOME:-$HOME/.cache}/wallpaper_picker/online/
├── authoritative_request
├── publication.lock
├── generations/
│   └── <request-generation>/
│       ├── manifest.json
│       ├── search_map.txt
│       └── previews/
└── current -> generations/<request-generation>
```

Compatibility links preserve the existing QML-facing paths:

```text
${XDG_CACHE_HOME:-$HOME/.cache}/wallpaper_picker/search_thumbs
${XDG_CACHE_HOME:-$HOME/.cache}/wallpaper_picker/search_map.txt
```

A generation is built completely in isolation. Validated previews, the active map and the manifest are finished before an `fcntl`-protected step replaces the atomic publication pointer.

The previous successful generation remains active when:

- API retrieval fails
- no candidate survives ranking
- every preview fails
- the request is interrupted
- the request becomes stale
- manifest creation fails
- publication fails

Cleanup occurs only after successful publication. It preserves the active generation and a bounded rollback set.

## Selected full-resolution download

Full-resolution files download only after the user selects an online result.

`WallpaperPicker.qml` starts the downloader through a Quickshell `Process` with separate arguments:

```text
bash
scripts/online_search.sh
--download
<selected-file-name>
--destination
<wallpaper-directory>/<selected-file-name>
```

The downloader:

1. resolves the exact filename in the active validated map
2. validates the Wallhaven full-resolution URL
3. downloads to a temporary file on the destination filesystem
4. rejects oversized or invalid responses
5. validates JPEG, PNG or WebP bytes and dimensions
6. flushes the completed file
7. atomically replaces the destination

On success, QML continues into the established thumbnail, lock-screen, `awww`, Matugen and optional ML4W flow.

On failure, QML:

- clears the active download and apply locks
- displays `Download failed`
- does not apply a missing or partial file
- does not close the picker as though the operation succeeded
- keeps the online cache unchanged
- allows another selection or search

An already-downloaded online result bypasses the network download and continues through the same application helper.

## Failure guarantees

The system is designed so that failures do not convert into partial success:

- failed retrieval does not clear the previous successful online generation
- failed preview validation does not publish partial cache state
- stale searches do not publish
- failed selected downloads do not replace an existing destination
- failed selected downloads do not trigger wallpaper application
- duplicate selection is blocked while a download is active
- local image and video paths remain independent of the online downloader

## Configuration reference

Invalid values fail clearly rather than being silently corrected.

| Variable | Default | Validation | Effect |
| --- | --- | --- | --- |
| `QS_WALLPAPER_TARGET_WIDTH` | detected width or `1920` | `1..16384`; must be paired with target height | Target display width |
| `QS_WALLPAPER_TARGET_HEIGHT` | detected height or `1080` | `1..16384`; must be paired with target width | Target display height |
| `QS_WALLPAPER_RESULT_LIMIT` | `12` | `1..24`; cannot exceed candidate limit | Maximum published previews |
| `QS_WALLPAPER_CANDIDATE_LIMIT` | `72` | `3..72`; at least result limit | Total raw candidate budget |
| `QS_WALLPAPER_SEARCH_JOBS` | `6` | `1..16` | Maximum concurrent preview workers |
| `QS_WALLPAPER_MIN_WIDTH` | target width | `1..16384` | Minimum candidate width |
| `QS_WALLPAPER_MIN_HEIGHT` | target height | `1..16384` | Minimum candidate height |
| `QS_WALLPAPER_MAX_RATIO_ERROR` | `0.20` | `0.01..0.75` | Maximum fractional ratio mismatch |
| `QS_WALLPAPER_CONNECT_TIMEOUT` | `8` seconds | `1..30`; not above total timeout | Connection timeout |
| `QS_WALLPAPER_TOTAL_TIMEOUT` | `30` seconds | `2..120`; at least connection timeout | Overall retrieval and preview deadline |
| `QS_WALLPAPER_RETRIES` | `1` | `0..3` | Bounded API retry count |

`QS_WALLPAPER_SEARCH_LIMIT` remains accepted as a legacy result-limit fallback when `QS_WALLPAPER_RESULT_LIMIT` is unset.

The local wallpaper directory is configured through `config/Settings.qml` or `QS_WALLPAPER_DIR` where the launcher and supporting scripts accept it.

## Networking and privacy

An explicit online search sends the normalized user-entered query and configured display constraints to Wallhaven. It downloads validated Wallhaven previews. Selecting a result may download its full-resolution Wallhaven image.

The feature does not send local wallpaper filenames or personal account data. It requires no Wallhaven account, API key, cloud AI service or GPU model.

Metadata ranking improves measurable display fit and source quality. It does not claim to understand subjective artistic quality or perform AI aesthetic analysis.

## Dependencies

### Runtime

- Quickshell
- Python 3.11 or newer
- Bash
- `awww`
- `mpvpaper` for video wallpapers

### Optional desktop integrations

- ImageMagick
- Matugen
- ML4W
- `hyprctl`, `wlr-randr` or `xrandr`

### Development and testing

- Python standard library
- Bash
- Git

Required CI does not contact Wallhaven.

## Testing

Run the network-independent suite:

```bash
python -m unittest discover -s tests -v
```

Compile Python sources and tests:

```bash
python -m compileall -q scripts tests
```

Validate tracked shell entry points:

```bash
while IFS= read -r script; do
  bash -n "$script"
done < <(git ls-files 'scripts/*.sh')
```

The GitHub workflow additionally verifies:

- exact feature-SHA checkout
- QML downloader argument wiring
- absence of the legacy inline full-resolution curl path
- successful-download continuation
- failed-download non-application
- duplicate-download locking
- deterministic ranking
- preview validation and backfilling
- stale and interrupted generation handling
- previous-cache preservation
- atomic selected-download behavior
- shell executable bits
- whitespace integrity
- personal-path, credential and broad process-kill regressions
- documentation and production consistency

## Preserved desktop behavior

The online feature is required to preserve:

- local image browsing and application
- local video browsing, preview and application
- original video filename resolution
- keyboard navigation
- animated cards and filters
- color filters and extraction
- wallpaper restoration
- lock-screen image updates
- Matugen integration
- optional ML4W synchronization
- the established wallpaper backend behavior

## Credits

The quality engine builds on **bay0n**'s original online-search contribution. Existing co-author attribution remains preserved in Git history.
