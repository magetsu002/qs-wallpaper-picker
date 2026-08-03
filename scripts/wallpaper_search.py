#!/usr/bin/env python3
"""Deterministic Wallhaven discovery for qs-wallpaper-picker.

The module is standard-library only. It keeps the shell/QML interface stable
while separating request authority, candidate normalization, deterministic
ranking and cache publication into testable production functions.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import ipaddress
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

API_URL = "https://wallhaven.cc/api/v1/search"
USER_AGENT = "qs-wallpaper-picker/3.0"
MAX_QUERY_LENGTH = 160
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_RESULT_LIMIT = 12
DEFAULT_CANDIDATE_LIMIT = 72
DEFAULT_JOBS = 6
DEFAULT_CONNECT_TIMEOUT = 8.0
DEFAULT_TOTAL_TIMEOUT = 30.0
DEFAULT_RETRIES = 1
DEFAULT_MAX_RATIO_ERROR = 0.20
MAX_RESULT_LIMIT = 24
MAX_CANDIDATE_LIMIT = 72
MAX_JOBS = 16

API_HOSTS = frozenset({"wallhaven.cc"})
FULL_IMAGE_HOSTS = frozenset({"w.wallhaven.cc"})
PREVIEW_HOSTS = frozenset({"th.wallhaven.cc"})
ALLOWED_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
ID_PATTERN = re.compile(r"^[A-Za-z0-9]{2,32}$")

STRATEGIES = (
    ("relevance", "relevance", None),
    ("toplist", "toplist", "1M"),
    ("favorites", "favorites", None),
)
STRATEGY_PRIORITY = {
    "relevance": 0,
    "toplist": 1,
    "favorites": 2,
}
STRATEGY_BASE_SCORE = {
    "relevance": 24.0,
    "toplist": 22.0,
    "favorites": 20.0,
}


class SearchError(RuntimeError):
    """User-facing online search failure."""


class CandidateRejected(SearchError):
    """Raised when one candidate violates a hard quality rule."""


class StaleRequest(SearchError):
    """Raised when an older request attempts to publish."""


@dataclass(frozen=True)
class RuntimeConfig:
    target_width: int
    target_height: int
    result_limit: int
    candidate_limit: int
    jobs: int
    min_width: int
    min_height: int
    max_ratio_error: float
    connect_timeout: float
    total_timeout: float
    retries: int


@dataclass(frozen=True)
class CacheLayout:
    root: Path
    online: Path
    generations: Path
    current: Path
    lock: Path
    authority: Path
    legacy_thumbs: Path
    legacy_map: Path

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> "CacheLayout":
        values = os.environ if env is None else env
        home = Path(values.get("HOME") or str(Path.home()))
        cache_home = Path(values.get("XDG_CACHE_HOME") or home / ".cache")
        root = cache_home / "wallpaper_picker"
        online = root / "online"
        return cls(
            root=root,
            online=online,
            generations=online / "generations",
            current=online / "current",
            lock=online / "publication.lock",
            authority=online / "authoritative_request",
            legacy_thumbs=root / "search_thumbs",
            legacy_map=root / "search_map.txt",
        )


@dataclass
class Candidate:
    wallpaper_id: str
    full_url: str
    preview_url: str
    width: int
    height: int
    file_size: int | None
    favorites: int | None
    views: int | None
    file_name: str
    sources: dict[str, int] = field(default_factory=dict)
    ratio_error: float = 0.0
    ratio_score: float = 0.0
    resolution_score: float = 0.0
    source_score: float = 0.0
    popularity_score: float = 0.0
    file_size_score: float = 0.0
    total_score: float = 0.0

    def as_manifest(self) -> dict[str, Any]:
        return {
            "id": self.wallpaper_id,
            "file_name": self.file_name,
            "full_url": self.full_url,
            "preview_url": self.preview_url,
            "width": self.width,
            "height": self.height,
            "file_size": self.file_size,
            "favorites": self.favorites,
            "views": self.views,
            "sources": [
                {
                    "name": name,
                    "position": self.sources[name],
                }
                for name in sorted(
                    self.sources,
                    key=lambda value: STRATEGY_PRIORITY[value],
                )
            ],
            "score": round(self.total_score, 6),
            "score_components": {
                "source": round(self.source_score, 6),
                "ratio": round(self.ratio_score, 6),
                "resolution": round(self.resolution_score, 6),
                "popularity": round(self.popularity_score, 6),
                "file_size": round(self.file_size_score, 6),
            },
        }


def normalize_query(raw: str) -> str:
    query = " ".join(str(raw or "").strip().split())
    if not query:
        raise SearchError("Search query is empty.")
    if len(query) > MAX_QUERY_LENGTH:
        raise SearchError(
            f"Search query exceeds the {MAX_QUERY_LENGTH}-character limit."
        )
    return query


def _positive_int(
    name: str,
    raw: str | None,
    default: int,
    *,
    minimum: int = 1,
    maximum: int,
) -> int:
    if raw is None or raw == "":
        return default
    if not re.fullmatch(r"[0-9]+", raw):
        raise SearchError(f"{name} must be an integer.")
    value = int(raw)
    if not minimum <= value <= maximum:
        raise SearchError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _positive_float(
    name: str,
    raw: str | None,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise SearchError(f"{name} must be numeric.") from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise SearchError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _optional_nonnegative_int(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _required_positive_int(name: str, raw: Any) -> int:
    if isinstance(raw, bool):
        raise CandidateRejected(f"{name} is invalid.")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise CandidateRejected(f"{name} is invalid.") from exc
    if value <= 0:
        raise CandidateRejected(f"{name} must be positive.")
    return value


def _parse_dimensions_text(text: str) -> tuple[int, int] | None:
    matches = re.findall(r"(?<!\d)(\d{3,5})\s*[xX]\s*(\d{3,5})(?!\d)", text)
    for width_raw, height_raw in matches:
        width = int(width_raw)
        height = int(height_raw)
        if 320 <= width <= 16384 and 240 <= height <= 16384:
            return width, height
    return None


def detect_display_dimensions(
    env: Mapping[str, str] | None = None,
    command_runner: Any = subprocess.run,
) -> tuple[int, int]:
    values = os.environ if env is None else env
    width_raw = values.get("QS_WALLPAPER_TARGET_WIDTH")
    height_raw = values.get("QS_WALLPAPER_TARGET_HEIGHT")

    if bool(width_raw) != bool(height_raw):
        raise SearchError(
            "QS_WALLPAPER_TARGET_WIDTH and QS_WALLPAPER_TARGET_HEIGHT "
            "must be set together."
        )
    if width_raw and height_raw:
        width = _positive_int(
            "QS_WALLPAPER_TARGET_WIDTH", width_raw, DEFAULT_WIDTH, maximum=16384
        )
        height = _positive_int(
            "QS_WALLPAPER_TARGET_HEIGHT", height_raw, DEFAULT_HEIGHT, maximum=16384
        )
        return width, height

    commands = (
        ("hyprctl", "monitors", "-j"),
        ("wlr-randr",),
        ("xrandr", "--current"),
    )
    for command in commands:
        try:
            completed = command_runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            continue
        if completed.returncode != 0:
            continue

        if command[0] == "hyprctl":
            try:
                payload = json.loads(completed.stdout)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, list):
                focused = next(
                    (
                        monitor
                        for monitor in payload
                        if isinstance(monitor, dict) and monitor.get("focused")
                    ),
                    None,
                )
                monitor = focused or next(
                    (item for item in payload if isinstance(item, dict)),
                    None,
                )
                if monitor:
                    width = monitor.get("width")
                    height = monitor.get("height")
                    if (
                        isinstance(width, int)
                        and isinstance(height, int)
                        and 320 <= width <= 16384
                        and 240 <= height <= 16384
                    ):
                        return width, height

        parsed = _parse_dimensions_text(completed.stdout)
        if parsed:
            return parsed

    return DEFAULT_WIDTH, DEFAULT_HEIGHT


def load_runtime_config(
    env: Mapping[str, str] | None = None,
    command_runner: Any = subprocess.run,
) -> RuntimeConfig:
    values = os.environ if env is None else env
    width, height = detect_display_dimensions(values, command_runner)

    result_limit = _positive_int(
        "QS_WALLPAPER_RESULT_LIMIT",
        values.get("QS_WALLPAPER_RESULT_LIMIT")
        or values.get("QS_WALLPAPER_SEARCH_LIMIT"),
        DEFAULT_RESULT_LIMIT,
        maximum=MAX_RESULT_LIMIT,
    )
    candidate_limit = _positive_int(
        "QS_WALLPAPER_CANDIDATE_LIMIT",
        values.get("QS_WALLPAPER_CANDIDATE_LIMIT"),
        DEFAULT_CANDIDATE_LIMIT,
        minimum=len(STRATEGIES),
        maximum=MAX_CANDIDATE_LIMIT,
    )
    if result_limit > candidate_limit:
        raise SearchError(
            "QS_WALLPAPER_RESULT_LIMIT cannot exceed "
            "QS_WALLPAPER_CANDIDATE_LIMIT."
        )

    jobs = _positive_int(
        "QS_WALLPAPER_SEARCH_JOBS",
        values.get("QS_WALLPAPER_SEARCH_JOBS"),
        DEFAULT_JOBS,
        maximum=MAX_JOBS,
    )
    min_width = _positive_int(
        "QS_WALLPAPER_MIN_WIDTH",
        values.get("QS_WALLPAPER_MIN_WIDTH"),
        width,
        maximum=16384,
    )
    min_height = _positive_int(
        "QS_WALLPAPER_MIN_HEIGHT",
        values.get("QS_WALLPAPER_MIN_HEIGHT"),
        height,
        maximum=16384,
    )
    max_ratio_error = _positive_float(
        "QS_WALLPAPER_MAX_RATIO_ERROR",
        values.get("QS_WALLPAPER_MAX_RATIO_ERROR"),
        DEFAULT_MAX_RATIO_ERROR,
        minimum=0.01,
        maximum=0.75,
    )
    connect_timeout = _positive_float(
        "QS_WALLPAPER_CONNECT_TIMEOUT",
        values.get("QS_WALLPAPER_CONNECT_TIMEOUT"),
        DEFAULT_CONNECT_TIMEOUT,
        minimum=1.0,
        maximum=30.0,
    )
    total_timeout = _positive_float(
        "QS_WALLPAPER_TOTAL_TIMEOUT",
        values.get("QS_WALLPAPER_TOTAL_TIMEOUT"),
        DEFAULT_TOTAL_TIMEOUT,
        minimum=2.0,
        maximum=120.0,
    )
    if connect_timeout > total_timeout:
        raise SearchError(
            "QS_WALLPAPER_CONNECT_TIMEOUT cannot exceed "
            "QS_WALLPAPER_TOTAL_TIMEOUT."
        )
    retries = _positive_int(
        "QS_WALLPAPER_RETRIES",
        values.get("QS_WALLPAPER_RETRIES"),
        DEFAULT_RETRIES,
        minimum=0,
        maximum=3,
    )

    return RuntimeConfig(
        target_width=width,
        target_height=height,
        result_limit=result_limit,
        candidate_limit=candidate_limit,
        jobs=jobs,
        min_width=min_width,
        min_height=min_height,
        max_ratio_error=max_ratio_error,
        connect_timeout=connect_timeout,
        total_timeout=total_timeout,
        retries=retries,
    )


@contextlib.contextmanager
def publication_lock(layout: CacheLayout) -> Iterable[None]:
    layout.online.mkdir(parents=True, exist_ok=True)
    with layout.lock.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_request_id(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return 0


def claim_request(layout: CacheLayout) -> int:
    with publication_lock(layout):
        request_id = _read_request_id(layout.authority) + 1
        _atomic_write_text(layout.authority, f"{request_id}\n")
        return request_id


def invalidate_requests(layout: CacheLayout) -> int:
    return claim_request(layout)


def is_authoritative(layout: CacheLayout, request_id: int) -> bool:
    with publication_lock(layout):
        return _read_request_id(layout.authority) == request_id


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def validate_url(url: str, purpose: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(str(url).strip())
    except ValueError as exc:
        raise CandidateRejected("Malformed URL.") from exc

    if parsed.scheme != "https":
        raise CandidateRejected("Only HTTPS URLs are allowed.")
    if parsed.username is not None or parsed.password is not None:
        raise CandidateRejected("URLs with embedded credentials are not allowed.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise CandidateRejected("URL port is invalid.") from exc
    if port not in (None, 443):
        raise CandidateRejected("Unexpected URL port.")

    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise CandidateRejected("URL host is missing.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
    ):
        raise CandidateRejected("Private or local network URLs are not allowed.")

    allowed_hosts = {
        "api": API_HOSTS,
        "full": FULL_IMAGE_HOSTS,
        "preview": PREVIEW_HOSTS,
    }.get(purpose)
    if allowed_hosts is None or host not in allowed_hosts:
        raise CandidateRejected(f"Unexpected {purpose} URL host.")

    if purpose == "api" and parsed.path != "/api/v1/search":
        raise CandidateRejected("Unexpected Wallhaven API path.")
    if purpose == "full" and not parsed.path.startswith("/full/"):
        raise CandidateRejected("Unexpected full-resolution path.")
    if purpose == "preview" and not parsed.path.startswith(("/lg/", "/orig/", "/small/")):
        raise CandidateRejected("Unexpected preview path.")

    return urllib.parse.urlunsplit(parsed)


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1

    def __init__(self, purpose: str):
        super().__init__()
        self.purpose = purpose

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl, self.purpose)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(url: str, purpose: str, timeout: float):
    validated = validate_url(url, purpose)
    opener = urllib.request.build_opener(SafeRedirectHandler(purpose))
    request = urllib.request.Request(
        validated,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json" if purpose == "api" else "image/*",
        },
    )
    response = opener.open(request, timeout=timeout)
    validate_url(response.geturl(), purpose)
    return response


def _request_json(
    url: str,
    *,
    connect_timeout: float,
    total_timeout: float,
    retries: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + total_timeout
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            with _open_url(
                url,
                "api",
                min(connect_timeout, remaining),
            ) as response:
                payload = response.read()
            parsed = json.loads(payload.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise SearchError("Wallhaven returned an invalid response.")
            return parsed
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            CandidateRejected,
        ) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(0.25 * (2**attempt), max(0.0, deadline - time.monotonic())))

    raise SearchError(f"Wallhaven request failed: {last_error or 'timeout'}")


def _strategy_counts(candidate_limit: int) -> list[int]:
    remaining = candidate_limit
    counts: list[int] = []
    for index in range(len(STRATEGIES)):
        slots = len(STRATEGIES) - index
        count = min(24, math.ceil(remaining / slots))
        counts.append(count)
        remaining -= count
    return counts


def build_strategy_requests(
    query: str,
    config: RuntimeConfig,
) -> list[tuple[str, str]]:
    requests: list[tuple[str, str]] = []
    for (name, sorting, top_range), per_page in zip(
        STRATEGIES,
        _strategy_counts(config.candidate_limit),
        strict=True,
    ):
        params = {
            "q": query,
            "purity": "100",
            "sorting": sorting,
            "order": "desc",
            "per_page": str(per_page),
            "atleast": f"{config.min_width}x{config.min_height}",
        }
        if top_range is not None:
            params["topRange"] = top_range
        url = f"{API_URL}?{urllib.parse.urlencode(params)}"
        validate_url(url, "api")
        requests.append((name, url))
    return requests


def retrieve_payloads(
    query: str,
    config: RuntimeConfig,
) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    deadline = time.monotonic() + config.total_timeout

    for name, url in build_strategy_requests(query, config):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SearchError("Wallhaven candidate retrieval timed out.")
        payloads[name] = _request_json(
            url,
            connect_timeout=min(config.connect_timeout, remaining),
            total_timeout=remaining,
            retries=config.retries,
        )
    return payloads


def _safe_filename(wallpaper_id: str, full_url: str) -> str:
    suffix = Path(urllib.parse.urlparse(full_url).path).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        suffix = ".jpg"
    return f"wallhaven-{wallpaper_id}{suffix}"


def normalize_candidate(
    raw: Mapping[str, Any],
    strategy: str,
    position: int,
    config: RuntimeConfig,
) -> Candidate:
    wallpaper_id = str(raw.get("id") or "").strip()
    if not ID_PATTERN.fullmatch(wallpaper_id):
        raise CandidateRejected("Wallpaper ID is missing or malformed.")

    full_url = validate_url(str(raw.get("path") or ""), "full")
    thumbs = raw.get("thumbs")
    if not isinstance(thumbs, Mapping):
        raise CandidateRejected("Preview metadata is missing.")
    preview_url = validate_url(
        str(
            thumbs.get("large")
            or thumbs.get("original")
            or thumbs.get("small")
            or ""
        ),
        "preview",
    )

    width = _required_positive_int("dimension_x", raw.get("dimension_x"))
    height = _required_positive_int("dimension_y", raw.get("dimension_y"))
    if width < config.min_width or height < config.min_height:
        raise CandidateRejected("Wallpaper is below the minimum dimensions.")

    target_landscape = config.target_width >= config.target_height
    if target_landscape and width < height:
        raise CandidateRejected("Portrait wallpaper rejected for landscape target.")
    if not target_landscape and height < width:
        raise CandidateRejected("Landscape wallpaper rejected for portrait target.")

    target_ratio = config.target_width / config.target_height
    candidate_ratio = width / height
    ratio_error = abs(candidate_ratio - target_ratio) / target_ratio
    if ratio_error > config.max_ratio_error:
        raise CandidateRejected("Wallpaper aspect ratio is outside the allowed range.")

    file_size = _optional_nonnegative_int(raw.get("file_size"))
    if raw.get("file_size") is not None and file_size is None:
        file_size = None
    if file_size == 0:
        raise CandidateRejected("Wallpaper file size is invalid.")

    candidate = Candidate(
        wallpaper_id=wallpaper_id,
        full_url=full_url,
        preview_url=preview_url,
        width=width,
        height=height,
        file_size=file_size,
        favorites=_optional_nonnegative_int(raw.get("favorites")),
        views=_optional_nonnegative_int(raw.get("views")),
        file_name=_safe_filename(wallpaper_id, full_url),
        sources={strategy: position},
        ratio_error=ratio_error,
    )
    _score_candidate(candidate, config)
    return candidate


def _source_score(sources: Mapping[str, int]) -> float:
    best = max(STRATEGY_BASE_SCORE[name] for name in sources)
    cross_source_bonus = 3.0 * max(0, len(sources) - 1)
    return min(30.0, best + cross_source_bonus)


def _ratio_score(ratio_error: float, max_ratio_error: float) -> float:
    return 25.0 * max(0.0, 1.0 - ratio_error / max_ratio_error)


def _resolution_score(candidate: Candidate, config: RuntimeConfig) -> float:
    scale = min(
        candidate.width / config.target_width,
        candidate.height / config.target_height,
    )
    return 20.0 * min(1.0, max(0.0, scale - 1.0))


def _popularity_score(favorites: int | None, views: int | None) -> float:
    if favorites is None and views is None:
        return 3.0

    favorite_points = 0.0
    view_points = 0.0
    efficiency_points = 0.0

    if favorites is not None:
        favorite_points = 9.0 * min(
            1.0,
            math.log1p(favorites) / math.log1p(5000),
        )
    if views is not None:
        view_points = 4.0 * min(
            1.0,
            math.log1p(views) / math.log1p(5_000_000),
        )
    if favorites is not None and views is not None:
        efficiency_points = 2.0 * min(
            1.0,
            (favorites / max(1, views)) * 100.0,
        )

    return min(15.0, favorite_points + view_points + efficiency_points)


def _file_size_score(file_size: int | None, pixels: int) -> float:
    if file_size is None:
        return 5.0

    bytes_per_pixel = file_size / pixels
    if bytes_per_pixel < 0.005 or bytes_per_pixel > 10.0:
        raise CandidateRejected(
            "Wallpaper file size is unreasonable for its resolution."
        )
    if bytes_per_pixel < 0.03:
        return 10.0 * (bytes_per_pixel - 0.005) / 0.025
    if bytes_per_pixel <= 0.8:
        return 10.0
    if bytes_per_pixel <= 3.0:
        return 10.0 * (1.0 - (bytes_per_pixel - 0.8) / 2.2)
    return max(0.0, 2.0 * (1.0 - (bytes_per_pixel - 3.0) / 7.0))


def _score_candidate(candidate: Candidate, config: RuntimeConfig) -> None:
    candidate.source_score = _source_score(candidate.sources)
    candidate.ratio_score = _ratio_score(
        candidate.ratio_error,
        config.max_ratio_error,
    )
    candidate.resolution_score = _resolution_score(candidate, config)
    candidate.popularity_score = _popularity_score(
        candidate.favorites,
        candidate.views,
    )
    candidate.file_size_score = _file_size_score(
        candidate.file_size,
        candidate.width * candidate.height,
    )
    candidate.total_score = (
        candidate.source_score
        + candidate.ratio_score
        + candidate.resolution_score
        + candidate.popularity_score
        + candidate.file_size_score
    )


def _intrinsic_preference(candidate: Candidate) -> tuple[Any, ...]:
    return (
        candidate.width * candidate.height,
        min(candidate.width, candidate.height),
        candidate.favorites if candidate.favorites is not None else -1,
        candidate.views if candidate.views is not None else -1,
        candidate.wallpaper_id,
        candidate.full_url,
        candidate.preview_url,
    )


def _merge_candidate(existing: Candidate, incoming: Candidate, config: RuntimeConfig) -> Candidate:
    merged_sources = dict(existing.sources)
    for source, position in incoming.sources.items():
        merged_sources[source] = min(position, merged_sources.get(source, position))

    preferred = max((existing, incoming), key=_intrinsic_preference)
    preferred.sources = merged_sources
    _score_candidate(preferred, config)
    return preferred


def rank_payloads(
    payloads: Mapping[str, Mapping[str, Any]],
    config: RuntimeConfig,
) -> list[Candidate]:
    normalized: list[Candidate] = []

    for strategy, _, _ in STRATEGIES:
        payload = payloads.get(strategy, {})
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, list):
            continue
        for position, raw in enumerate(data, start=1):
            if not isinstance(raw, Mapping):
                continue
            try:
                normalized.append(
                    normalize_candidate(raw, strategy, position, config)
                )
            except CandidateRejected:
                continue

    normalized.sort(
        key=lambda candidate: (
            candidate.wallpaper_id,
            candidate.full_url,
            candidate.preview_url,
            min(STRATEGY_PRIORITY[name] for name in candidate.sources),
        )
    )

    unique: list[Candidate] = []
    by_id: dict[str, int] = {}
    by_full: dict[str, int] = {}
    by_preview: dict[str, int] = {}

    for candidate in normalized:
        duplicate_indexes = {
            index
            for index in (
                by_id.get(candidate.wallpaper_id),
                by_full.get(candidate.full_url),
                by_preview.get(candidate.preview_url),
            )
            if index is not None
        }

        if duplicate_indexes:
            index = min(duplicate_indexes)
            merged = _merge_candidate(unique[index], candidate, config)
            unique[index] = merged
        else:
            index = len(unique)
            unique.append(candidate)

        current = unique[index]
        by_id[current.wallpaper_id] = index
        by_full[current.full_url] = index
        by_preview[current.preview_url] = index

    unique.sort(
        key=lambda candidate: (
            -round(candidate.total_score, 9),
            -round(candidate.ratio_score, 9),
            -round(candidate.resolution_score, 9),
            min(STRATEGY_PRIORITY[name] for name in candidate.sources),
            candidate.wallpaper_id,
        )
    )
    return unique[: config.candidate_limit]


def _is_probable_image(data: bytes) -> bool:
    if not data:
        return False
    if data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    return data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP"


def _download_preview(url: str, destination: Path, timeout: float) -> bool:
    try:
        with _open_url(url, "preview", timeout) as response:
            data = response.read()
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        CandidateRejected,
    ):
        return False
    if not _is_probable_image(data):
        return False
    destination.write_bytes(data)
    return True


def _replace_symlink(path: Path, target: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.link")
    with contextlib.suppress(FileNotFoundError):
        temp.unlink()
    os.symlink(target, temp)

    if path.exists() and not path.is_symlink():
        backup = path.with_name(f".{path.name}.legacy-{int(time.time())}")
        os.replace(path, backup)
    elif path.is_symlink():
        path.unlink()

    os.replace(temp, path)


def publish_generation(
    layout: CacheLayout,
    request_id: int,
    generation: Path,
) -> None:
    with publication_lock(layout):
        if _read_request_id(layout.authority) != request_id:
            raise StaleRequest("Search result became stale before publication.")

        relative_target = os.path.relpath(generation, layout.online)
        current_temp = layout.online / f".current.{request_id}.tmp"
        with contextlib.suppress(FileNotFoundError):
            current_temp.unlink()
        os.symlink(relative_target, current_temp)
        os.replace(current_temp, layout.current)

        _replace_symlink(layout.legacy_thumbs, "online/current/previews")
        _replace_symlink(layout.legacy_map, "online/current/search_map.txt")


def search(query_raw: str, env: Mapping[str, str] | None = None) -> list[str]:
    query = normalize_query(query_raw)
    config = load_runtime_config(env)
    layout = CacheLayout.from_environment(env)
    layout.generations.mkdir(parents=True, exist_ok=True)

    request_id = claim_request(layout)
    generation = Path(
        tempfile.mkdtemp(
            prefix=f"{request_id:012d}-",
            dir=layout.generations,
        )
    )
    previews = generation / "previews"
    previews.mkdir()

    try:
        payloads = retrieve_payloads(query, config)
        ranked = rank_payloads(payloads, config)

        published: list[Candidate] = []
        for candidate in ranked:
            if len(published) >= config.result_limit:
                break
            preview_path = previews / candidate.file_name
            if _download_preview(
                candidate.preview_url,
                preview_path,
                config.total_timeout,
            ):
                published.append(candidate)

        if not published:
            raise SearchError("No valid online previews were returned.")

        manifest = {
            "schema_version": 1,
            "request_id": request_id,
            "query": query,
            "target": {
                "width": config.target_width,
                "height": config.target_height,
            },
            "status": "online_results",
            "results": [candidate.as_manifest() for candidate in published],
        }
        _atomic_write_text(
            generation / "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
        map_text = "".join(
            f"{candidate.file_name}|{candidate.full_url}\n"
            for candidate in published
        )
        _atomic_write_text(generation / "search_map.txt", map_text)

        publish_generation(layout, request_id, generation)
        return [
            f"{candidate.file_name}|{candidate.full_url}"
            for candidate in published
        ]
    except Exception:
        shutil.rmtree(generation, ignore_errors=True)
        raise


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "query",
        nargs="?",
        help="Wallhaven query. Omit only with --invalidate.",
    )
    parser.add_argument(
        "--invalidate",
        action="store_true",
        help="Invalidate any in-flight request without clearing the cache.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    layout = CacheLayout.from_environment()

    try:
        if args.invalidate:
            invalidate_requests(layout)
            return 0
        if args.query is None:
            raise SearchError("Search query is required.")
        for line in search(args.query):
            print(line)
        return 0
    except StaleRequest:
        return 75
    except SearchError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
