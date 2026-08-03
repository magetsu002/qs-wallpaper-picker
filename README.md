# QS Wallpaper Picker

A keyboard-first Quickshell wallpaper picker for local images, local videos and explicit Wallhaven discovery on Linux desktops.

The picker preserves the original animated card interface, color filters, video previews, wallpaper restoration, Matugen integration and optional ML4W synchronization while adding a deterministic online discovery engine.

## Search behavior

### Local search

1. Open the **Search** filter.
2. Type a query.
3. Local wallpaper filenames are filtered immediately while you type.

Local filenames remain on the machine. They are not added to Wallhaven requests.

### Online search

The UI displays the instruction:

```text
Type to search locally • Press Enter to search online
```

Pressing Enter explicitly searches Wallhaven for the current non-empty query, even when matching local files exist. The interface distinguishes local results, online availability, online searching, online results, empty local results, empty online results and online failures.

Changing the query invalidates an older request. Request generations are monotonically ordered, and both the QML consumer and cache publisher reject stale results. Cancelling the old process is only an optimization; correctness does not depend on process termination.

## Online discovery pipeline

The stable QML-facing entry point is:

```text
scripts/online_search.sh
```

It delegates to two standard-library-only Python modules:

```text
scripts/wallpaper_search.py   # configuration, retrieval, filtering and ranking
scripts/preview_pipeline.py   # preview validation and transactional publication
```

No API key, cloud AI model, GPU model or third-party Python package is required.

### Retrieval

Each query uses three deterministic, bounded Wallhaven strategies:

1. relevance
2. toplist over the supported one-month range
3. favorites

Every request is SFW, uses the same normalized query, requests at most 24 candidates and applies the configured minimum dimensions. The default total raw candidate budget is 72. There is no uncontrolled pagination or random sorting.

Only HTTPS Wallhaven API and media hosts are accepted. Embedded credentials, unexpected ports, local addresses, private-network addresses, unrelated hosts and unsupported paths are rejected. Redirects are bounded and the final URL is validated again.

### Hard rejection rules

Candidates are rejected when they contain or represent:

- a missing or malformed wallpaper ID
- a missing or unsafe full-resolution URL
- a missing or unsafe preview URL
- missing, zero or negative dimensions
- dimensions below the configured minimum
- portrait media for a landscape display target, or the inverse
- aspect-ratio error beyond the configured threshold
- duplicate wallpaper IDs
- duplicate full-resolution URLs
- duplicate preview URLs for the same asset
- malformed required metadata
- clearly unreasonable file-size-to-pixel characteristics when reliable size metadata exists

Favorites, views and file size are optional. Missing optional popularity metadata receives a conservative score rather than causing rejection.

## Deterministic quality score

Every accepted candidate receives one score out of 100:

| Component | Maximum | Behavior |
| --- | ---: | --- |
| Retrieval source quality | 30 | Rewards relevance, toplist or favorites retrieval and bounded cross-source agreement. |
| Aspect-ratio and crop fit | 25 | Exact target-ratio matches score highest; the score falls to zero at the configured ratio-error boundary. |
| Resolution surplus | 20 | Barely sufficient images receive little surplus credit; comfortably oversized images reach the cap. |
| Favorites and view efficiency | 15 | Uses bounded `log1p` normalization plus a capped favorite-to-view efficiency term. |
| File-size sanity | 10 | Rewards plausible bytes-per-pixel values without rewarding arbitrarily huge files. |

The final ordering is deterministic:

1. total score descending
2. aspect-ratio score descending
3. resolution score descending
4. best retrieval-source priority
5. wallpaper ID ascending

API response order, request completion order and preview completion order do not determine the displayed order.

## Preview validation and backfilling

Previews are downloaded only after ranking. The pipeline processes bounded ranked waves using the configured job count until the displayed-result limit has valid previews or the ranked pool is exhausted.

The validator rejects:

- empty or zero-byte bodies
- HTML, JSON, XML and other obvious text error responses
- unsupported file signatures
- malformed or truncated JPEG, PNG and WebP headers
- invalid dimensions
- previews below the minimum useful preview size
- responses larger than the preview safety limit

The `Content-Type` header is considered but is never trusted by itself. ImageMagick is not required for basic validation.

Only selected, validated previews remain in a published generation. A failed higher-ranked preview is backfilled by the next valid ranked candidate without allowing thread completion order to reorder results.

## Cache model

The online cache uses immutable generation directories and one atomic publication pointer:

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

Compatibility links keep the existing QML paths working:

```text
${XDG_CACHE_HOME:-$HOME/.cache}/wallpaper_picker/search_thumbs
${XDG_CACHE_HOME:-$HOME/.cache}/wallpaper_picker/search_map.txt
```

A new generation is built in isolation. Its previews and manifest are completed before an `fcntl`-protected publication step atomically replaces `current`. A stale request cannot publish while holding the lock.

The previous successful generation remains active when retrieval fails, ranking produces no valid candidate, every preview fails, the request is interrupted, manifest creation fails or publication is rejected. Old inactive generations are removed only after successful publication, while the active generation and a bounded rollback set are retained.

## Download behavior

- Preview files download during an explicit online search.
- Full-resolution files download only after the user selects an online result.
- Full-resolution URLs originate from the validated active result map.
- The failure-safe pipeline exposes an atomic selected-download interface:

```bash
scripts/online_search.sh \
  --download wallhaven-<id>.<extension> \
  --destination /path/to/final/wallpaper
```

That interface validates the selected map entry and Wallhaven host, downloads into a temporary file on the destination filesystem, validates JPEG/PNG/WebP content and dimensions, flushes the file and atomically replaces the destination. A failed or obvious error response cannot replace an existing valid destination.

### Current draft limitation

The repository still contains a legacy inline full-resolution download block inside `WallpaperPicker.qml`. The safe `--download` interface is implemented and tested, but the inline block must be switched to that interface before this draft can be declared fully certified. The draft pull request and issue evidence must keep this limitation explicit until repaired.

## Configuration

Invalid values fail with a clear error. Values are not silently converted from negative, zero, nonnumeric or unreasonable input.

| Variable | Default | Valid range and relationships | Effect | Invalid-value behavior |
| --- | --- | --- | --- | --- |
| `QS_WALLPAPER_TARGET_WIDTH` | detected display width or `1920` | `1..16384`; must be set with target height | Target display width used for filtering and scoring | Search fails clearly |
| `QS_WALLPAPER_TARGET_HEIGHT` | detected display height or `1080` | `1..16384`; must be set with target width | Target display height used for filtering and scoring | Search fails clearly |
| `QS_WALLPAPER_RESULT_LIMIT` | `12` | `1..24`; cannot exceed candidate limit | Maximum validated previews displayed | Search fails clearly |
| `QS_WALLPAPER_CANDIDATE_LIMIT` | `72` | `3..72`; must be at least result limit | Total raw candidate budget across strategies | Search fails clearly |
| `QS_WALLPAPER_SEARCH_JOBS` | `6` | `1..16` | Maximum concurrent preview workers | Search fails clearly |
| `QS_WALLPAPER_MIN_WIDTH` | target width | `1..16384` | Minimum accepted wallpaper width | Search fails clearly |
| `QS_WALLPAPER_MIN_HEIGHT` | target height | `1..16384` | Minimum accepted wallpaper height | Search fails clearly |
| `QS_WALLPAPER_MAX_RATIO_ERROR` | `0.20` | `0.01..0.75` | Maximum fractional aspect-ratio difference | Search fails clearly |
| `QS_WALLPAPER_CONNECT_TIMEOUT` | `8` seconds | `1..30`; cannot exceed total timeout | Per-connection timeout bound | Search fails clearly |
| `QS_WALLPAPER_TOTAL_TIMEOUT` | `30` seconds | `2..120`; must be at least connection timeout | Total retrieval/preview deadline | Search fails clearly |
| `QS_WALLPAPER_RETRIES` | `1` | `0..3` | Bounded API retry count | Search fails clearly |

The legacy `QS_WALLPAPER_SEARCH_LIMIT` variable is accepted as a result-limit fallback when `QS_WALLPAPER_RESULT_LIMIT` is unset.

## Dependencies

### Required runtime dependencies

- Quickshell
- Python 3.11 or newer
- Bash
- a supported wallpaper backend already used by the picker, such as `awww`
- `mpvpaper` for local video wallpapers

### Required test dependencies

- Python standard library
- Bash
- Git

### Optional validation and desktop tools

- ImageMagick for existing thumbnail and color workflows
- Matugen for dynamic color generation
- ML4W files and tools when ML4W synchronization is enabled
- `hyprctl`, `wlr-randr` or `xrandr` for display-size detection; otherwise the safe fallback is used

## Testing

Run the network-independent suite:

```bash
python -m unittest discover -s tests -v
```

Additional checks used by CI include:

```bash
python -m compileall -q scripts tests
find scripts -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n
git diff --check
```

Tests exercise production normalization, ranking, URL validation, preview validation, deterministic ordering, backfilling, stale publication, interrupted cleanup, previous-cache preservation and atomic selected downloads. Required CI does not contact Wallhaven.

## Privacy and networking

An explicit online search sends only the normalized user-entered query and configured display constraints to Wallhaven. It downloads Wallhaven preview images for validated results. A selected result may then download its full-resolution Wallhaven image.

The feature does not require:

- a Wallhaven account
- a Wallhaven API key for ordinary SFW searches
- local wallpaper filenames in remote queries
- personal account data
- a cloud AI service
- a GPU model

Metadata ranking improves measurable display fit and source quality. It does not claim to understand subjective artistic quality or perform AI aesthetic analysis.

## Existing desktop behavior

The feature is designed to preserve:

- local image browsing
- local video browsing and previews
- original video filename resolution
- keyboard navigation
- animated filter and card behavior
- color filters
- wallpaper restoration
- Matugen integration
- optional ML4W synchronization
- wallpaper application through the existing desktop backend

## Credits

QS Wallpaper Picker was created by **Magetsu**.

The original online-search contribution was implemented by **bay0n**. The existing co-author attribution is preserved in Git history, and this quality engine builds on that work.

## License

See [LICENSE](LICENSE).
