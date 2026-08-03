from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import wallpaper_search as core  # noqa: E402
import preview_pipeline as pipeline  # noqa: E402


def base_env() -> dict[str, str]:
    return {
        "QS_WALLPAPER_TARGET_WIDTH": "1920",
        "QS_WALLPAPER_TARGET_HEIGHT": "1080",
        "QS_WALLPAPER_RESULT_LIMIT": "12",
        "QS_WALLPAPER_CANDIDATE_LIMIT": "72",
        "QS_WALLPAPER_SEARCH_JOBS": "6",
        "QS_WALLPAPER_CONNECT_TIMEOUT": "8",
        "QS_WALLPAPER_TOTAL_TIMEOUT": "30",
        "QS_WALLPAPER_RETRIES": "1",
        "QS_WALLPAPER_MAX_RATIO_ERROR": "0.20",
    }


def make_raw(wallpaper_id: str) -> dict:
    return {
        "id": wallpaper_id,
        "path": (
            f"https://w.wallhaven.cc/full/{wallpaper_id[:2]}/"
            f"wallhaven-{wallpaper_id}.jpg"
        ),
        "dimension_x": 3840,
        "dimension_y": 2160,
        "file_size": 3_000_000,
        "favorites": 100,
        "views": 10_000,
        "thumbs": {
            "large": (
                f"https://th.wallhaven.cc/lg/{wallpaper_id[:2]}/"
                f"{wallpaper_id}.jpg"
            )
        },
    }


def png_bytes(width: int = 640, height: int = 360) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


def jpeg_bytes(width: int = 640, height: int = 360) -> bytes:
    return (
        b"\xff\xd8\xff\xc0\x00\x11\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        + b"\xff\xd9"
    )


def webp_bytes(width: int = 640, height: int = 360) -> bytes:
    return (
        b"RIFF"
        + (22).to_bytes(4, "little")
        + b"WEBPVP8X"
        + (10).to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
        + (width - 1).to_bytes(3, "little")
        + (height - 1).to_bytes(3, "little")
    )


class PreviewPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = core.load_runtime_config(base_env())
        payloads = {
            "relevance": {
                "data": [make_raw(f"pv{i:04d}") for i in range(1, 7)]
            },
            "toplist": {"data": []},
            "favorites": {"data": []},
        }
        self.ranked = core.rank_payloads(payloads, self.config)

    def test_supported_image_headers_and_dimensions(self) -> None:
        self.assertEqual(
            pipeline.validate_image_bytes(png_bytes()),
            ("png", 640, 360),
        )
        self.assertEqual(
            pipeline.validate_image_bytes(jpeg_bytes()),
            ("jpeg", 640, 360),
        )
        self.assertEqual(
            pipeline.validate_image_bytes(webp_bytes()),
            ("webp", 640, 360),
        )

    def test_invalid_text_empty_corrupt_and_tiny_images_are_rejected(self) -> None:
        cases = [
            (b"", ""),
            (b"<html>error</html>", "text/html"),
            (b'{"error": true}', "application/json"),
            (b"not an image", "image/jpeg"),
            (png_bytes(100, 50), "image/png"),
            (b"\x89PNG\r\n\x1a\ntruncated", "image/png"),
        ]
        for data, content_type in cases:
            with self.subTest(data=data[:12]):
                with self.assertRaises(core.SearchError):
                    pipeline.validate_image_bytes(data, content_type)

    def test_preview_failures_backfill_in_rank_order(self) -> None:
        config = replace(self.config, result_limit=2, jobs=2)
        first, second, third, fourth = self.ranked[:4]

        def fetcher(url, purpose, timeout, max_bytes):
            if url == first.preview_url:
                return b"<html>failure</html>", "text/html"
            if url == second.preview_url:
                time.sleep(0.03)
            if url == third.preview_url:
                time.sleep(0.01)
            return png_bytes(), "image/png"

        with tempfile.TemporaryDirectory() as temp:
            previews = Path(temp)
            selected = pipeline.download_ranked_previews(
                [first, second, third, fourth],
                previews,
                config,
                fetcher=fetcher,
            )
            self.assertEqual(
                [item.wallpaper_id for item in selected],
                [second.wallpaper_id, third.wallpaper_id],
            )
            self.assertEqual(
                sorted(path.name for path in previews.iterdir()),
                sorted([second.file_name, third.file_name]),
            )

    def test_every_preview_failing_returns_empty_subset(self) -> None:
        config = replace(self.config, result_limit=2, jobs=2)
        with tempfile.TemporaryDirectory() as temp:
            selected = pipeline.download_ranked_previews(
                self.ranked[:4],
                Path(temp),
                config,
                fetcher=lambda *args: (b"{}", "application/json"),
            )
            self.assertEqual(selected, [])
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_stale_request_is_rejected_before_next_wave(self) -> None:
        config = replace(self.config, result_limit=1, jobs=1)
        with tempfile.TemporaryDirectory() as temp:
            env = {
                "HOME": temp,
                "XDG_CACHE_HOME": str(Path(temp) / "cache"),
            }
            layout = core.CacheLayout.from_environment(env)
            first = core.claim_request(layout)
            core.claim_request(layout)
            previews = Path(temp) / "previews"
            previews.mkdir()
            with self.assertRaises(core.StaleRequest):
                pipeline.download_ranked_previews(
                    self.ranked[:1],
                    previews,
                    config,
                    layout=layout,
                    request_id=first,
                    fetcher=lambda *args: (png_bytes(), "image/png"),
                )

    def test_publication_uses_one_pointer_and_preserves_previous(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = {
                "HOME": temp,
                "XDG_CACHE_HOME": str(Path(temp) / "cache"),
            }
            layout = core.CacheLayout.from_environment(env)
            layout.generations.mkdir(parents=True)
            request_id = core.claim_request(layout)
            first = layout.generations / "0001"
            (first / "previews").mkdir(parents=True)
            (first / "manifest.json").write_text("{}", encoding="utf-8")
            (first / "search_map.txt").write_text("", encoding="utf-8")
            pipeline.publish_generation(layout, request_id, first)
            original = os.readlink(layout.current)

            stale = layout.generations / "0002"
            (stale / "previews").mkdir(parents=True)
            core.claim_request(layout)
            with self.assertRaises(core.StaleRequest):
                pipeline.publish_generation(layout, request_id, stale)
            self.assertEqual(os.readlink(layout.current), original)
            self.assertTrue(layout.legacy_thumbs.is_symlink())
            self.assertTrue(layout.legacy_map.is_symlink())

    def test_generation_cleanup_keeps_active_and_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = {
                "HOME": temp,
                "XDG_CACHE_HOME": str(Path(temp) / "cache"),
            }
            layout = core.CacheLayout.from_environment(env)
            layout.generations.mkdir(parents=True)
            for index in range(6):
                (layout.generations / f"{index:04d}").mkdir()
            layout.online.mkdir(parents=True, exist_ok=True)
            os.symlink("generations/0001", layout.current)
            pipeline.cleanup_generations(layout, retain=3)
            remaining = {path.name for path in layout.generations.iterdir()}
            self.assertIn("0001", remaining)
            self.assertLessEqual(len(remaining), 4)
            self.assertTrue({"0003", "0004", "0005"}.issubset(remaining))

    def test_failed_search_preserves_previous_current_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = base_env()
            env.update({
                "HOME": temp,
                "XDG_CACHE_HOME": str(Path(temp) / "cache"),
            })
            layout = core.CacheLayout.from_environment(env)
            layout.generations.mkdir(parents=True)
            request_id = core.claim_request(layout)
            previous = layout.generations / "previous"
            (previous / "previews").mkdir(parents=True)
            (previous / "manifest.json").write_text("{}", encoding="utf-8")
            (previous / "search_map.txt").write_text("", encoding="utf-8")
            pipeline.publish_generation(layout, request_id, previous)
            original = os.readlink(layout.current)

            with mock.patch.object(
                core,
                "retrieve_payloads",
                side_effect=core.SearchError("offline"),
            ):
                with self.assertRaises(core.SearchError):
                    pipeline.search("city", env)
            self.assertEqual(os.readlink(layout.current), original)

    def test_interrupted_search_removes_unpublished_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = base_env()
            env.update({
                "HOME": temp,
                "XDG_CACHE_HOME": str(Path(temp) / "cache"),
            })
            layout = core.CacheLayout.from_environment(env)
            with mock.patch.object(
                core,
                "retrieve_payloads",
                side_effect=KeyboardInterrupt,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    pipeline.search("city", env)
            self.assertEqual(list(layout.generations.iterdir()), [])

    def test_full_download_failure_preserves_existing_destination(self) -> None:
        candidate = self.ranked[0]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            map_path = root / "map.txt"
            map_path.write_text(
                f"{candidate.file_name}|{candidate.full_url}\n",
                encoding="utf-8",
            )
            destination = root / candidate.file_name
            destination.write_bytes(b"existing")
            with self.assertRaises(core.SearchError):
                pipeline.download_full_wallpaper(
                    map_path,
                    candidate.file_name,
                    destination,
                    fetcher=lambda *args: (
                        b"<html>bad gateway</html>",
                        "text/html",
                    ),
                )
            self.assertEqual(destination.read_bytes(), b"existing")
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_full_download_validates_url_and_replaces_atomically(self) -> None:
        candidate = self.ranked[0]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            map_path = root / "map.txt"
            map_path.write_text(
                f"{candidate.file_name}|{candidate.full_url}\n",
                encoding="utf-8",
            )
            destination = root / candidate.file_name
            destination.write_bytes(b"old")
            pipeline.download_full_wallpaper(
                map_path,
                candidate.file_name,
                destination,
                fetcher=lambda *args: (
                    jpeg_bytes(1920, 1080),
                    "image/jpeg",
                ),
            )
            self.assertEqual(
                destination.read_bytes(),
                jpeg_bytes(1920, 1080),
            )

            map_path.write_text(
                f"{candidate.file_name}|https://evil.example/file.jpg\n",
                encoding="utf-8",
            )
            with self.assertRaises(core.CandidateRejected):
                pipeline.download_full_wallpaper(
                    map_path,
                    candidate.file_name,
                    destination,
                    fetcher=lambda *args: (jpeg_bytes(), "image/jpeg"),
                )


if __name__ == "__main__":
    unittest.main()
