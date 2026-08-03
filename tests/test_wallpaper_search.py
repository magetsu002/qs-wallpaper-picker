from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "wallpaper_search.py"

spec = importlib.util.spec_from_file_location("wallpaper_search", MODULE_PATH)
assert spec and spec.loader
wallpaper_search = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = wallpaper_search
spec.loader.exec_module(wallpaper_search)


class FoundationTests(unittest.TestCase):
    def test_query_normalization_and_limit(self) -> None:
        self.assertEqual(
            wallpaper_search.normalize_query("  neon   city  "),
            "neon city",
        )
        with self.assertRaises(wallpaper_search.SearchError):
            wallpaper_search.normalize_query("   ")
        with self.assertRaises(wallpaper_search.SearchError):
            wallpaper_search.normalize_query("x" * 161)

    def test_search_url_encodes_query(self) -> None:
        url = wallpaper_search.build_search_url("night city & rain", 12)
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["q"], ["night city & rain"])
        self.assertEqual(query["purity"], ["100"])
        self.assertEqual(query["sorting"], ["relevance"])
        self.assertEqual(query["per_page"], ["12"])

    def test_explicit_dimensions_must_be_valid_pair(self) -> None:
        with self.assertRaises(wallpaper_search.SearchError):
            wallpaper_search.detect_display_dimensions(
                {"QS_WALLPAPER_TARGET_WIDTH": "2560"}
            )

        self.assertEqual(
            wallpaper_search.detect_display_dimensions(
                {
                    "QS_WALLPAPER_TARGET_WIDTH": "2560",
                    "QS_WALLPAPER_TARGET_HEIGHT": "1600",
                }
            ),
            (2560, 1600),
        )

        with self.assertRaises(wallpaper_search.SearchError):
            wallpaper_search.detect_display_dimensions(
                {
                    "QS_WALLPAPER_TARGET_WIDTH": "-1",
                    "QS_WALLPAPER_TARGET_HEIGHT": "1080",
                }
            )

    def test_hyprctl_focused_monitor_detection(self) -> None:
        payload = json.dumps(
            [
                {"width": 1920, "height": 1080, "focused": False},
                {"width": 3440, "height": 1440, "focused": True},
            ]
        )

        def runner(command, **kwargs):
            if command[0] == "hyprctl":
                return SimpleNamespace(returncode=0, stdout=payload)
            raise FileNotFoundError

        self.assertEqual(
            wallpaper_search.detect_display_dimensions({}, runner),
            (3440, 1440),
        )

    def test_dimension_detection_falls_back_safely(self) -> None:
        def runner(command, **kwargs):
            raise FileNotFoundError

        self.assertEqual(
            wallpaper_search.detect_display_dimensions({}, runner),
            (1920, 1080),
        )

    def test_request_claims_are_monotonic_and_invalidation_is_authoritative(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = {
                "HOME": temp,
                "XDG_CACHE_HOME": str(Path(temp) / "cache"),
            }
            layout = wallpaper_search.CacheLayout.from_environment(env)
            first = wallpaper_search.claim_request(layout)
            second = wallpaper_search.invalidate_requests(layout)

            self.assertEqual(first, 1)
            self.assertEqual(second, 2)
            self.assertFalse(
                wallpaper_search.is_authoritative(layout, first)
            )
            self.assertTrue(
                wallpaper_search.is_authoritative(layout, second)
            )

    def test_stale_generation_cannot_replace_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = {
                "HOME": temp,
                "XDG_CACHE_HOME": str(Path(temp) / "cache"),
            }
            layout = wallpaper_search.CacheLayout.from_environment(env)
            layout.generations.mkdir(parents=True)

            first = wallpaper_search.claim_request(layout)
            first_generation = layout.generations / "first"
            (first_generation / "previews").mkdir(parents=True)
            (first_generation / "search_map.txt").write_text(
                "first.jpg|https://w.wallhaven.cc/full/aa/wallhaven-aa.jpg\n",
                encoding="utf-8",
            )
            wallpaper_search.publish_generation(
                layout,
                first,
                first_generation,
            )
            original_target = os.readlink(layout.current)

            second = wallpaper_search.claim_request(layout)
            self.assertEqual(second, first + 1)

            stale_generation = layout.generations / "stale"
            (stale_generation / "previews").mkdir(parents=True)
            (stale_generation / "search_map.txt").write_text(
                "stale.jpg|https://w.wallhaven.cc/full/bb/wallhaven-bb.jpg\n",
                encoding="utf-8",
            )

            with self.assertRaises(wallpaper_search.StaleRequest):
                wallpaper_search.publish_generation(
                    layout,
                    first,
                    stale_generation,
                )

            self.assertEqual(os.readlink(layout.current), original_target)

    def test_main_qml_exposes_search_instruction_and_explicit_enter(self) -> None:
        main_qml = (ROOT / "Main.qml").read_text(encoding="utf-8")
        self.assertIn(
            "Type to search locally • Press Enter to search online",
            main_qml,
        )
        self.assertIn('sequence: "Return"', main_qml)
        self.assertIn("picker.triggerOnlineSearch(normalized)", main_qml)
        self.assertIn("ONLINE RESULTS", main_qml)
        self.assertIn("NO LOCAL RESULTS", main_qml)
        self.assertIn("ONLINE SEARCH FAILED", main_qml)
        self.assertIn("--invalidate", main_qml)


if __name__ == "__main__":
    unittest.main()
