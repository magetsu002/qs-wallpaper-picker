#!/usr/bin/env python3
"""Failure-safe preview and full-resolution delivery for Wallhaven results."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import urllib.error
from pathlib import Path
from typing import Any, Mapping

import wallpaper_search as core

MAX_PREVIEW_BYTES = 16 * 1024 * 1024
MAX_FULL_BYTES = 128 * 1024 * 1024
DEFAULT_CACHE_RETENTION = 3

SearchError = core.SearchError
StaleRequest = core.StaleRequest
CandidateRejected = core.CandidateRejected
Candidate = core.Candidate
RuntimeConfig = core.RuntimeConfig
CacheLayout = core.CacheLayout


def _reject_text_payload(data: bytes, content_type: str = "") -> None:
    stripped = data.lstrip()[:64].lower()
    textual_type = content_type.lower().split(";", 1)[0].strip()
    if textual_type.startswith("text/") or textual_type in {
        "application/json",
        "application/problem+json",
        "application/xml",
    }:
        raise SearchError("Image request returned a textual response.")
    if stripped.startswith((b"<!doctype", b"<html", b"<?xml", b"{", b"[")):
        raise SearchError("Image request returned an error document.")


def image_dimensions(data: bytes, content_type: str = "") -> tuple[str, int, int]:
    """Validate JPEG, PNG or WebP bytes and return format and dimensions."""
    if not data:
        raise SearchError("Image response is empty.")
    _reject_text_payload(data, content_type)

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 33 or data[12:16] != b"IHDR":
            raise SearchError("PNG image is truncated or malformed.")
        width, height = struct.unpack(">II", data[16:24])
        if width <= 0 or height <= 0:
            raise SearchError("PNG image has invalid dimensions.")
        return "png", width, height

    if data.startswith(b"\xff\xd8"):
        offset = 2
        sof_markers = {
            0xC0, 0xC1, 0xC2, 0xC3,
            0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB,
            0xCD, 0xCE, 0xCF,
        }
        while offset < len(data):
            while offset < len(data) and data[offset] != 0xFF:
                offset += 1
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in {0xD8, 0xD9}:
                continue
            if marker == 0xDA:
                break
            if offset + 2 > len(data):
                break
            segment_length = int.from_bytes(data[offset:offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(data):
                raise SearchError("JPEG image is truncated or malformed.")
            if marker in sof_markers:
                if segment_length < 7:
                    raise SearchError("JPEG frame header is malformed.")
                height = int.from_bytes(data[offset + 3:offset + 5], "big")
                width = int.from_bytes(data[offset + 5:offset + 7], "big")
                if width <= 0 or height <= 0:
                    raise SearchError("JPEG image has invalid dimensions.")
                return "jpeg", width, height
            offset += segment_length
        raise SearchError("JPEG image has no valid frame header.")

    if data.startswith(b"RIFF") and len(data) >= 30 and data[8:12] == b"WEBP":
        declared_size = int.from_bytes(data[4:8], "little") + 8
        if declared_size > len(data):
            raise SearchError("WebP image is truncated.")
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
        elif chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
            bits = int.from_bytes(data[21:25], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
        elif chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
            width = int.from_bytes(data[26:28], "little") & 0x3FFF
            height = int.from_bytes(data[28:30], "little") & 0x3FFF
        else:
            raise SearchError("WebP image has an unsupported or malformed header.")
        if width <= 0 or height <= 0:
            raise SearchError("WebP image has invalid dimensions.")
        return "webp", width, height

    raise SearchError("Response is not a supported image.")


def validate_image_bytes(
    data: bytes,
    content_type: str = "",
    *,
    min_width: int = 200,
    min_height: int = 120,
) -> tuple[str, int, int]:
    image_format, width, height = image_dimensions(data, content_type)
    if width < min_width or height < min_height:
        raise SearchError("Image is too small to be useful.")
    return image_format, width, height


def fetch_image_bytes(
    url: str,
    purpose: str,
    timeout: float,
    max_bytes: int,
) -> tuple[bytes, str]:
    try:
        with core._open_url(url, purpose, timeout) as response:
            content_type = str(response.headers.get("Content-Type", ""))
            data = response.read(max_bytes + 1)
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        CandidateRejected,
    ) as exc:
        raise SearchError(f"Image download failed: {exc}") from exc
    if len(data) > max_bytes:
        raise SearchError("Image exceeds the configured safety limit.")
    return data, content_type


def _write_atomic(path: Path, data: bytes) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()


def _download_one_preview(
    index: int,
    candidate: Candidate,
    previews: Path,
    timeout: float,
    fetcher: Any,
) -> tuple[int, Candidate] | None:
    try:
        data, content_type = fetcher(
            candidate.preview_url,
            "preview",
            timeout,
            MAX_PREVIEW_BYTES,
        )
        validate_image_bytes(data, content_type)
        _write_atomic(previews / candidate.file_name, data)
        return index, candidate
    except (SearchError, OSError):
        return None


def download_ranked_previews(
    ranked: list[Candidate],
    previews: Path,
    config: RuntimeConfig,
    *,
    layout: CacheLayout | None = None,
    request_id: int | None = None,
    fetcher: Any = fetch_image_bytes,
) -> list[Candidate]:
    """Download bounded ranked waves until enough previews validate."""
    selected: list[tuple[int, Candidate]] = []
    deadline = time.monotonic() + config.total_timeout

    for start in range(0, len(ranked), config.jobs):
        if len(selected) >= config.result_limit:
            break
        if layout is not None and request_id is not None:
            if not core.is_authoritative(layout, request_id):
                raise StaleRequest("Search became stale during preview generation.")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        wave = list(enumerate(ranked[start:start + config.jobs], start=start))
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(config.jobs, len(wave))
        ) as executor:
            futures = [
                executor.submit(
                    _download_one_preview,
                    index,
                    candidate,
                    previews,
                    min(config.connect_timeout, remaining),
                    fetcher,
                )
                for index, candidate in wave
            ]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result is not None:
                    selected.append(result)

        selected.sort(key=lambda item: item[0])

    chosen = selected[: config.result_limit]
    chosen_names = {candidate.file_name for _, candidate in chosen}
    for path in previews.iterdir():
        if path.is_file() and path.name not in chosen_names:
            with contextlib.suppress(OSError):
                path.unlink()
    return [candidate for _, candidate in chosen]


def _replace_symlink(path: Path, target: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.link")
    with contextlib.suppress(FileNotFoundError):
        temp.unlink()
    os.symlink(target, temp)
    try:
        if path.exists() and not path.is_symlink():
            backup = path.with_name(f".{path.name}.legacy-{int(time.time())}")
            os.replace(path, backup)
        os.replace(temp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()


def _active_generation(layout: CacheLayout) -> Path | None:
    try:
        target = os.readlink(layout.current)
    except (FileNotFoundError, OSError):
        return None
    return (layout.online / target).resolve(strict=False)


def cleanup_generations(
    layout: CacheLayout,
    retain: int = DEFAULT_CACHE_RETENTION,
) -> None:
    """Keep the active generation plus a bounded rollback set."""
    if retain < 1 or not layout.generations.exists():
        return
    active = _active_generation(layout)
    directories = sorted(
        (
            entry
            for entry in layout.generations.iterdir()
            if entry.is_dir() and not entry.is_symlink()
        ),
        key=lambda entry: entry.name,
        reverse=True,
    )
    keep: set[Path] = {
        entry.resolve(strict=False) for entry in directories[:retain]
    }
    if active is not None:
        keep.add(active)
    for entry in directories:
        if entry.resolve(strict=False) in keep:
            continue
        with contextlib.suppress(OSError):
            shutil.rmtree(entry)


def publish_generation(
    layout: CacheLayout,
    request_id: int,
    generation: Path,
) -> None:
    with core.publication_lock(layout):
        if core._read_request_id(layout.authority) != request_id:
            raise StaleRequest("Search result became stale before publication.")

        relative_target = os.path.relpath(generation, layout.online)
        current_temp = layout.online / f".current.{request_id}.tmp"
        with contextlib.suppress(FileNotFoundError):
            current_temp.unlink()
        os.symlink(relative_target, current_temp)
        os.replace(current_temp, layout.current)

        _replace_symlink(layout.legacy_thumbs, "online/current/previews")
        _replace_symlink(layout.legacy_map, "online/current/search_map.txt")

    cleanup_generations(layout)


def resolve_download_url(map_path: Path, file_name: str) -> str:
    if Path(file_name).name != file_name or not file_name.startswith("wallhaven-"):
        raise SearchError("Wallpaper filename is invalid.")
    try:
        lines = map_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SearchError(f"Wallpaper map is unavailable: {exc}") from exc
    for line in lines:
        name, separator, url = line.partition("|")
        if separator and name == file_name:
            return core.validate_url(url, "full")
    raise SearchError("Selected wallpaper is not present in the active map.")


def download_full_wallpaper(
    map_path: Path,
    file_name: str,
    destination: Path,
    *,
    timeout: float = core.DEFAULT_TOTAL_TIMEOUT,
    fetcher: Any = fetch_image_bytes,
) -> Path:
    """Download one selected full-resolution wallpaper atomically."""
    url = resolve_download_url(map_path, file_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    data, content_type = fetcher(url, "full", timeout, MAX_FULL_BYTES)
    validate_image_bytes(
        data,
        content_type,
        min_width=320,
        min_height=240,
    )
    _write_atomic(destination, data)
    return destination


def search(query_raw: str, env: Mapping[str, str] | None = None) -> list[str]:
    query = core.normalize_query(query_raw)
    config = core.load_runtime_config(env)
    layout = CacheLayout.from_environment(env)
    layout.generations.mkdir(parents=True, exist_ok=True)

    request_id = core.claim_request(layout)
    generation = Path(
        tempfile.mkdtemp(
            prefix=f"{request_id:012d}-",
            dir=layout.generations,
        )
    )
    previews = generation / "previews"
    previews.mkdir()

    try:
        payloads = core.retrieve_payloads(query, config)
        ranked = core.rank_payloads(payloads, config)
        published = download_ranked_previews(
            ranked,
            previews,
            config,
            layout=layout,
            request_id=request_id,
        )
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
        core._atomic_write_text(
            generation / "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
        core._atomic_write_text(
            generation / "search_map.txt",
            "".join(
                f"{candidate.file_name}|{candidate.full_url}\n"
                for candidate in published
            ),
        )
        publish_generation(layout, request_id, generation)
        return [
            f"{candidate.file_name}|{candidate.full_url}"
            for candidate in published
        ]
    except BaseException:
        shutil.rmtree(generation, ignore_errors=True)
        raise


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("query", nargs="?")
    parser.add_argument("--invalidate", action="store_true")
    parser.add_argument("--download", metavar="FILE_NAME")
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--map", dest="map_path", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    layout = CacheLayout.from_environment()
    try:
        if args.invalidate:
            core.invalidate_requests(layout)
            return 0
        if args.download:
            if args.destination is None:
                raise SearchError("--destination is required with --download.")
            map_path = args.map_path or layout.legacy_map
            download_full_wallpaper(
                map_path,
                args.download,
                args.destination,
            )
            print(args.destination)
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
