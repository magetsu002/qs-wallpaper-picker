from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "wallpaper_search.py"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "wallhaven_candidates.json"

spec = importlib.util.spec_from_file_location("wallpaper_search", MODULE_PATH)
assert spec and spec.loader
wallpaper_search = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = wallpaper_search
spec.loader.exec_module(wallpaper_search)


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


def load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def make_raw(
    wallpaper_id: str,
    *,
    width: int = 3840,
    height: int = 2160,
    file_size: int | None = 3_000_000,
    favorites: int | None = 100,
    views: int | None = 10_000,
) -> dict:
    raw = {
        "id": wallpaper_id,
        "path": (
            f"https://w.wallhaven.cc/full/{wallpaper_id[:2]}/"
            f"wallhaven-{wallpaper_id}.jpg"
        ),
        "dimension_x": width,
        "dimension_y": height,
        "thumbs": {
            "large": (
                f"https://th.wallhaven.cc/lg/{wallpaper_id[:2]}/"
                f"{wallpaper_id}.jpg"
            )
        },
    }
    if file_size is not None:
        raw["file_size"] = file_size
    if favorites is not None:
        raw["favorites"] = favorites
    if views is not None:
        raw["views"] = views
    return raw


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

            wallpaper_search.claim_request(layout)
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


class ConfigurationAndRequestTests(unittest.TestCase):
    def test_strategy_requests_are_bounded_and_query_is_encoded(self) -> None:
        config = wallpaper_search.load_runtime_config(base_env())
        requests = wallpaper_search.build_strategy_requests(
            'night city; $(touch /tmp/nope) & rain',
            config,
        )
        self.assertEqual([name for name, _ in requests], [
            "relevance",
            "toplist",
            "favorites",
        ])

        total = 0
        for name, url in requests:
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            total += int(query["per_page"][0])
            self.assertLessEqual(int(query["per_page"][0]), 24)
            self.assertEqual(query["purity"], ["100"])
            self.assertEqual(
                query["q"],
                ['night city; $(touch /tmp/nope) & rain'],
            )
            self.assertEqual(parsed.hostname, "wallhaven.cc")
            if name == "toplist":
                self.assertEqual(query["topRange"], ["1M"])
        self.assertEqual(total, config.candidate_limit)

    def test_invalid_and_conflicting_configuration_values_fail(self) -> None:
        invalid_cases = [
            {"QS_WALLPAPER_RESULT_LIMIT": "0"},
            {"QS_WALLPAPER_CANDIDATE_LIMIT": "73"},
            {"QS_WALLPAPER_SEARCH_JOBS": "-1"},
            {"QS_WALLPAPER_MAX_RATIO_ERROR": "nan"},
            {
                "QS_WALLPAPER_CONNECT_TIMEOUT": "31",
                "QS_WALLPAPER_TOTAL_TIMEOUT": "30",
            },
            {
                "QS_WALLPAPER_RESULT_LIMIT": "13",
                "QS_WALLPAPER_CANDIDATE_LIMIT": "12",
            },
        ]
        for overrides in invalid_cases:
            env = base_env()
            env.update(overrides)
            with self.subTest(overrides=overrides):
                with self.assertRaises(wallpaper_search.SearchError):
                    wallpaper_search.load_runtime_config(env)

    def test_url_allowlist_rejects_unsafe_targets(self) -> None:
        unsafe = [
            ("http://w.wallhaven.cc/full/aa/wallhaven-aa.jpg", "full"),
            ("https://evil.example/full/aa/wallhaven-aa.jpg", "full"),
            ("https://127.0.0.1/full/aa/wallhaven-aa.jpg", "full"),
            ("https://w.wallhaven.cc:444/full/aa/wallhaven-aa.jpg", "full"),
            ("https://user:pass@w.wallhaven.cc/full/aa/wallhaven-aa.jpg", "full"),
            ("https://th.wallhaven.cc/not-a-preview/aa.jpg", "preview"),
        ]
        for url, purpose in unsafe:
            with self.subTest(url=url):
                with self.assertRaises(wallpaper_search.CandidateRejected):
                    wallpaper_search.validate_url(url, purpose)


class RankingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = wallpaper_search.load_runtime_config(base_env())
        self.payloads = load_fixture()
        self.ranked = wallpaper_search.rank_payloads(
            self.payloads,
            self.config,
        )
        self.by_id = {
            candidate.wallpaper_id: candidate
            for candidate in self.ranked
        }

    def test_exact_ratio_ranks_above_near_ratio(self) -> None:
        self.assertGreater(
            self.by_id["exct01"].ratio_score,
            self.by_id["near01"].ratio_score,
        )
        self.assertGreater(
            self.by_id["exct01"].total_score,
            self.by_id["near01"].total_score,
        )

    def test_hard_filters_reject_ratio_resolution_portrait_and_bad_size(self) -> None:
        ids = {candidate.wallpaper_id for candidate in self.ranked}
        self.assertNotIn("bad001", ids)
        self.assertNotIn("low001", ids)
        self.assertNotIn("port01", ids)
        self.assertNotIn("tiny01", ids)

    def test_duplicate_id_and_urls_are_removed(self) -> None:
        full_urls = [candidate.full_url for candidate in self.ranked]
        self.assertEqual(
            len(full_urls),
            len(set(full_urls)),
        )
        self.assertEqual(
            sum(candidate.wallpaper_id == "dupe01" for candidate in self.ranked),
            1,
        )

    def test_combined_retrieval_sources_improve_bounded_source_score(self) -> None:
        combined = self.by_id["dupe01"]
        single = self.by_id["top001"]
        self.assertEqual(set(combined.sources), {
            "relevance",
            "toplist",
            "favorites",
        })
        self.assertGreater(combined.source_score, single.source_score)
        self.assertLessEqual(combined.source_score, 30.0)

    def test_missing_optional_popularity_is_retained_conservatively(self) -> None:
        missing = self.by_id["miss01"]
        self.assertIsNone(missing.favorites)
        self.assertIsNone(missing.views)
        self.assertEqual(missing.popularity_score, 3.0)

    def test_popularity_normalization_is_monotonic_and_bounded(self) -> None:
        low = wallpaper_search._popularity_score(10, 1_000)
        high = wallpaper_search._popularity_score(10_000_000, 1_000_000_000)
        self.assertGreater(high, low)
        self.assertLessEqual(high, 15.0)

    def test_deterministic_order_is_independent_of_payload_list_order(self) -> None:
        shuffled = copy.deepcopy(self.payloads)
        for payload in shuffled.values():
            payload["data"].reverse()

        reranked = wallpaper_search.rank_payloads(
            shuffled,
            self.config,
        )
        self.assertEqual(
            [candidate.wallpaper_id for candidate in self.ranked],
            [candidate.wallpaper_id for candidate in reranked],
        )

    def test_final_tie_breaker_is_wallpaper_id(self) -> None:
        payloads = {
            "relevance": {
                "data": [
                    make_raw("bb0001"),
                    make_raw("aa0001"),
                ]
            },
            "toplist": {"data": []},
            "favorites": {"data": []},
        }
        ranked = wallpaper_search.rank_payloads(payloads, self.config)
        self.assertEqual(
            [candidate.wallpaper_id for candidate in ranked],
            ["aa0001", "bb0001"],
        )

    def test_candidate_limit_is_enforced(self) -> None:
        config = replace(
            self.config,
            candidate_limit=3,
            result_limit=3,
        )
        ranked = wallpaper_search.rank_payloads(
            self.payloads,
            config,
        )
        self.assertLessEqual(len(ranked), 3)

    def test_malformed_required_metadata_and_unsafe_urls_are_rejected(self) -> None:
        invalid = make_raw("badurl")
        invalid["path"] = "https://example.com/file.jpg"
        with self.assertRaises(wallpaper_search.CandidateRejected):
            wallpaper_search.normalize_candidate(
                invalid,
                "relevance",
                1,
                self.config,
            )

        missing_id = make_raw("valid1")
        missing_id["id"] = ""
        with self.assertRaises(wallpaper_search.CandidateRejected):
            wallpaper_search.normalize_candidate(
                missing_id,
                "relevance",
                1,
                self.config,
            )


if __name__ == "__main__":
    unittest.main()
