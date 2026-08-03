#!/usr/bin/env python3
"""Wallhaven search foundation for qs-wallpaper-picker.

Milestone 1 establishes:
- safe query normalization
- monotonic request generations
- stale publication rejection under an fcntl lock
- display-size detection with validated environment overrides
- immutable generation directories with one atomic current pointer
- backward-compatible output for the existing QML consumer

Later milestones extend ranking and preview validation without changing the CLI.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

API_URL = "https://wallhaven.cc/api/v1/search"
USER_AGENT = "qs-wallpaper-picker/3.0"
MAX_QUERY_LENGTH = 160
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_RESULT_LIMIT = 24
DEFAULT_JOBS = 6
DEFAULT_CONNECT_TIMEOUT = 8.0
DEFAULT_TOTAL_TIMEOUT = 30.0
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


class SearchError(RuntimeError):
    """User-facing online search failure."""


class StaleRequest(SearchError):
    """Raised when an older request attempts to publish."""


@dataclass(frozen=True)
class RuntimeConfig:
    target_width: int
    target_height: int
    result_limit: int
    jobs: int
    connect_timeout: float
    total_timeout: float


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
    def from_environment(cls, env: dict[str, str] | None = None) -> "CacheLayout":
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
    if not minimum <= value <= maximum:
        raise SearchError(f"{name} must be between {minimum} and {maximum}.")
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
    env: dict[str, str] | None = None,
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
    env: dict[str, str] | None = None,
    command_runner: Any = subprocess.run,
) -> RuntimeConfig:
    values = os.environ if env is None else env
    width, height = detect_display_dimensions(values, command_runner)
    result_limit = _positive_int(
        "QS_WALLPAPER_RESULT_LIMIT",
        values.get("QS_WALLPAPER_RESULT_LIMIT")
        or values.get("QS_WALLPAPER_SEARCH_LIMIT"),
        DEFAULT_RESULT_LIMIT,
        maximum=24,
    )
    jobs = _positive_int(
        "QS_WALLPAPER_SEARCH_JOBS",
        values.get("QS_WALLPAPER_SEARCH_JOBS"),
        DEFAULT_JOBS,
        maximum=16,
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
    return RuntimeConfig(
        target_width=width,
        target_height=height,
        result_limit=result_limit,
        jobs=jobs,
        connect_timeout=connect_timeout,
        total_timeout=total_timeout,
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


def _request_json(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SearchError(f"Wallhaven request failed: {exc}") from exc
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SearchError("Wallhaven returned invalid JSON.") from exc
    if not isinstance(parsed, dict):
        raise SearchError("Wallhaven returned an invalid response.")
    return parsed


def build_search_url(query: str, result_limit: int) -> str:
    params = urllib.parse.urlencode(
        {
            "q": query,
            "purity": "100",
            "sorting": "relevance",
            "order": "desc",
            "per_page": str(result_limit),
        }
    )
    return f"{API_URL}?{params}"


def _safe_filename(wallpaper_id: str, full_url: str) -> str:
    suffix = Path(urllib.parse.urlparse(full_url).path).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        suffix = ".jpg"
    return f"wallhaven-{wallpaper_id}{suffix}"


def _is_probable_image(data: bytes) -> bool:
    if not data:
        return False
    if data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    return data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP"


def _download_preview(url: str, destination: Path, timeout: float) -> bool:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
    if not _is_probable_image(data):
        return False
    destination.write_bytes(data)
    return True


def normalize_basic_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []

    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    for raw in data:
        if not isinstance(raw, dict):
            continue
        wallpaper_id = str(raw.get("id") or "").strip()
        full_url = str(raw.get("path") or "").strip()
        thumbs = raw.get("thumbs")
        preview_url = ""
        if isinstance(thumbs, dict):
            preview_url = str(
                thumbs.get("large")
                or thumbs.get("original")
                or thumbs.get("small")
                or ""
            ).strip()
        if not wallpaper_id or not full_url or not preview_url:
            continue
        if wallpaper_id in seen_ids or full_url in seen_urls:
            continue
        seen_ids.add(wallpaper_id)
        seen_urls.add(full_url)
        results.append(
            {
                "id": wallpaper_id,
                "full_url": full_url,
                "preview_url": preview_url,
                "file_name": _safe_filename(wallpaper_id, full_url),
            }
        )
    return results


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


def search(query_raw: str, env: dict[str, str] | None = None) -> list[str]:
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
        payload = _request_json(
            build_search_url(query, config.result_limit),
            config.total_timeout,
        )
        candidates = normalize_basic_candidates(payload)

        published: list[dict[str, Any]] = []
        for candidate in candidates[: config.result_limit]:
            preview_path = previews / candidate["file_name"]
            if _download_preview(
                candidate["preview_url"],
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
            "results": published,
        }
        _atomic_write_text(
            generation / "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
        map_text = "".join(
            f"{item['file_name']}|{item['full_url']}\n" for item in published
        )
        _atomic_write_text(generation / "search_map.txt", map_text)

        publish_generation(layout, request_id, generation)
        return [
            f"{item['file_name']}|{item['full_url']}" for item in published
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
