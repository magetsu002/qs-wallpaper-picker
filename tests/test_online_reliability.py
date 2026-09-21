from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import wallpaper_search as core
import preview_pipeline as pipeline


def base_env() -> dict[str, str]:
    return {
        "QS_WALLPAPER_TARGET_WIDTH": "1920",
        "QS_WALLPAPER_TARGET_HEIGHT": "1080",
        "QS_WALLPAPER_RESULT_LIMIT": "2",
        "QS_WALLPAPER_CANDIDATE_LIMIT": "6",
        "QS_WALLPAPER_SEARCH_JOBS": "2",
        "QS_WALLPAPER_CONNECT_TIMEOUT": "2",
        "QS_WALLPAPER_TOTAL_TIMEOUT": "5",
        "QS_WALLPAPER_RETRIES": "1",
        "QS_WALLPAPER_MAX_RATIO_ERROR": "0.20",
    }


def extract_block(text: str, anchor: str) -> str:
    start = text.index(anchor)
    brace = text.index("{", start)
    depth = 0
    for index in range(brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    raise AssertionError(f"unterminated block: {anchor}")


class QmlRoutingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main = (ROOT / "Main.qml").read_text(encoding="utf-8")
        cls.picker = (ROOT / "WallpaperPicker.qml").read_text(encoding="utf-8")
    def test_enter_routes_only_through_canonical_submit(self) -> None:
        search_input = extract_block(self.picker, "TextInput {\n                    id: searchInput")
        shortcut = extract_block(self.main, 'Shortcut {\n        sequence: "Return"')
        self.assertIn("onAccepted: {\n                        window.submitOnlineSearch()", search_input)
        self.assertIn("picker.submitOnlineSearch()", shortcut)
        self.assertEqual(self.picker.count("function submitOnlineSearch()"), 1)
        self.assertNotIn("function triggerLocalSearch()", self.picker)

    def test_typing_is_local_only_and_invalidates_inflight_search(self) -> None:
        edited = extract_block(self.picker, "onTextEdited:")
        self.assertIn("window.cancelOnlineSearch()", edited)
        self.assertIn("window.applyFilters(true)", edited)
        self.assertIn("window.isOnlineSearch = false", edited)
        self.assertNotIn("triggerOnlineSearch(", edited)
        self.assertNotIn("submitOnlineSearch(", edited)

    def test_stale_child_exit_is_guarded_before_ui_mutation(self) -> None:
        process = extract_block(self.picker, "Process {\n        id: onlineSearchProcess")
        cancel = extract_block(self.picker, "function cancelOnlineSearch()")
        trigger = extract_block(self.picker, "function triggerOnlineSearch(query)")
        guard = "window.activeOnlineSearchEpoch !== window.onlineSearchEpoch"
        self.assertIn(guard, process)
        self.assertLess(process.index(guard), process.index("window.isSearchingOnline = false"))
        self.assertLess(process.index(guard), process.index("window.applyOnlineResults"))
        self.assertIn("window.pendingOnlineSearchQuery", process)
        self.assertIn("Qt.callLater(() => window.triggerOnlineSearch(nextQuery))", process)
        self.assertNotIn("onlineSearchProcess.running = false", cancel)
        self.assertIn("window.pendingOnlineSearchQuery = normalized", trigger)

    def test_failed_newer_search_preserves_previous_online_model(self) -> None:
        process = extract_block(self.picker, "Process {\n        id: onlineSearchProcess")
        self.assertIn(
            "window.isOnlineSearch =\n                window.onlineSearchStartedFromPublishedResults",
            process,
        )
        failure_tail = process[process.index("if (exitCode === 75)"):]
        self.assertNotIn("localProxyModel.clear()", failure_tail)
        self.assertIn("window.isSearchingOnline = false", process)

    def test_success_replaces_results(self) -> None:
        apply_results = extract_block(self.picker, "function applyOnlineResults(text)")
        self.assertIn("localProxyModel.clear()", apply_results)
        self.assertIn("localProxyModel.append(", apply_results)
        self.assertIn("window.isOnlineSearch = true", apply_results)

    def test_calm_error_messages_are_exposed(self) -> None:
        classifier = extract_block(self.picker, "function onlineSearchErrorMessage(text)")
        for message in (
            "Wallpaper service temporarily unavailable",
            "Wallpaper service timed out",
            "Network unavailable",
            "Online search failed",
        ):
            self.assertIn(message, classifier)


class ProviderClassificationTests(unittest.TestCase):
    def test_http_429_is_nonretryable(self) -> None:
        error = urllib.error.HTTPError("https://wallhaven.cc", 429, "rate", {}, None)
        classified = core._classify_provider_error(error)
        self.assertEqual(classified.code, "provider_rate_limited")
        self.assertEqual(classified.http_status, 429)
        self.assertFalse(classified.retryable)
        self.assertEqual(
            classified.user_message,
            "Wallpaper service temporarily unavailable",
        )

    def test_transient_http_5xx_are_retryable(self) -> None:
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                error = urllib.error.HTTPError(
                    "https://wallhaven.cc", status, "server", {}, None
                )
                classified = core._classify_provider_error(error)
                self.assertEqual(classified.code, "provider_unavailable")
                self.assertEqual(classified.http_status, status)
                self.assertTrue(classified.retryable)

    def test_network_dns_reset_and_timeout_are_classified(self) -> None:
        dns = core._classify_provider_error(
            urllib.error.URLError(socket.gaierror(-2, "name resolution failed"))
        )
        self.assertEqual(dns.code, "dns_failure")
        self.assertTrue(dns.retryable)

        reset = core._classify_provider_error(ConnectionResetError("reset"))
        self.assertEqual(reset.code, "network_unavailable")
        self.assertTrue(reset.retryable)

        timeout = core._classify_provider_error(TimeoutError("timed out"))
        self.assertEqual(timeout.code, "timeout")
        self.assertTrue(timeout.retryable)

    def test_503_retries_are_bounded(self) -> None:
        error = urllib.error.HTTPError(
            "https://wallhaven.cc", 503, "unavailable", {}, None
        )
        with mock.patch.object(core, "_open_url", side_effect=error) as opened:
            with self.assertRaises(core.SearchError) as raised:
                core._request_json(
                    "https://wallhaven.cc/api/v1/search?q=test",
                    connect_timeout=1,
                    total_timeout=3,
                    retries=1,
                )
        self.assertEqual(raised.exception.code, "provider_unavailable")
        self.assertEqual(opened.call_count, 2)

    def test_429_is_not_retried(self) -> None:
        error = urllib.error.HTTPError(
            "https://wallhaven.cc", 429, "rate", {}, None
        )
        with mock.patch.object(core, "_open_url", side_effect=error) as opened:
            with self.assertRaises(core.SearchError):
                core._request_json(
                    "https://wallhaven.cc/api/v1/search?q=test",
                    connect_timeout=1,
                    total_timeout=3,
                    retries=3,
                )
        self.assertEqual(opened.call_count, 1)

    def test_retryable_network_failures_are_bounded(self) -> None:
        failures = (
            urllib.error.URLError(socket.gaierror(-2, "dns")),
            ConnectionResetError("reset"),
            TimeoutError("timeout"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(core, "_open_url", side_effect=failure) as opened:
                    with self.assertRaises(core.SearchError):
                        core._request_json(
                            "https://wallhaven.cc/api/v1/search?q=test",
                            connect_timeout=1,
                            total_timeout=3,
                            retries=1,
                        )
                self.assertEqual(opened.call_count, 2)

    def test_malformed_json_is_not_retried(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = b"not-json"
        with mock.patch.object(core, "_open_url", return_value=response) as opened:
            with self.assertRaises(core.SearchError) as raised:
                core._request_json(
                    "https://wallhaven.cc/api/v1/search?q=test",
                    connect_timeout=1,
                    total_timeout=3,
                    retries=3,
                )
        self.assertEqual(raised.exception.code, "malformed_response")
        self.assertEqual(opened.call_count, 1)



def make_candidate() -> core.Candidate:
    return core.Candidate(
        wallpaper_id="abc123",
        full_url="https://w.wallhaven.cc/full/ab/wallhaven-abc123.jpg",
        preview_url="https://th.wallhaven.cc/lg/ab/abc123.jpg",
        width=3840,
        height=2160,
        file_size=3_000_000,
        favorites=10,
        views=1000,
        file_name="wallhaven-abc123.jpg",
        sources={"relevance": 1},
    )


def seed_generation(layout: core.CacheLayout, name: str = "previous") -> str:
    layout.generations.mkdir(parents=True, exist_ok=True)
    request_id = core.claim_request(layout)
    generation = layout.generations / name
    (generation / "previews").mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "request_id": request_id,
        "query": "previous",
        "status": "online_results",
        "results": [],
    }
    (generation / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (generation / "search_map.txt").write_text("", encoding="utf-8")
    pipeline.publish_generation(layout, request_id, generation)
    return os.readlink(layout.current)


class DiagnosticsPublicationTests(unittest.TestCase):
    def test_success_lifecycle_is_single_request_and_publishes_exact_map(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = base_env()
            env.update({
                "HOME": temp,
                "XDG_CACHE_HOME": str(Path(temp) / "cache"),
            })
            layout = core.CacheLayout.from_environment(env)
            candidate = make_candidate()

            with mock.patch.object(core, "retrieve_payloads", return_value={"relevance": {"data": []}}), \
                 mock.patch.object(core, "rank_payloads", return_value=[candidate]), \
                 mock.patch.object(pipeline, "download_ranked_previews", return_value=[candidate]):
                result = pipeline.search("  City   Lights  ", env)

            self.assertEqual(result, [f"{candidate.file_name}|{candidate.full_url}"])
            current = (layout.online / os.readlink(layout.current)).resolve()
            self.assertEqual(
                (current / "search_map.txt").read_text(encoding="utf-8"),
                f"{candidate.file_name}|{candidate.full_url}\n",
            )
            self.assertEqual(
                pipeline.resolve_download_url(
                    current / "search_map.txt", candidate.file_name
                ),
                candidate.full_url,
            )

            events = [
                json.loads(line)
                for line in layout.diagnostics.read_text(encoding="utf-8").splitlines()
            ]
            stages = [event["stage"] for event in events]
            self.assertEqual(stages, [
                "REQUESTED",
                "PROVIDER_CONNECTING",
                "PROVIDER_RESPONSE",
                "RANKING",
                "PREVIEWING",
                "PUBLISHING",
                "PUBLISHED",
            ])
            request_ids = {event["request_id"] for event in events}
            self.assertEqual(len(request_ids), 1)
            self.assertTrue(all(event["query"] == "City Lights" for event in events))
            self.assertTrue(all(event["provider"] == "wallhaven" for event in events))
            self.assertTrue(events[-1]["authoritative"])
            self.assertEqual(events[-1]["terminal_outcome"], "success")
    def test_provider_503_preserves_previous_generation_and_logs_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = base_env()
            env.update({
                "HOME": temp,
                "XDG_CACHE_HOME": str(Path(temp) / "cache"),
            })
            layout = core.CacheLayout.from_environment(env)
            previous = seed_generation(layout)
            failure = core.SearchError(
                "Wallhaven returned HTTP 503.",
                code="provider_unavailable",
                user_message="Wallpaper service temporarily unavailable",
                http_status=503,
                retryable=True,
            )

            with mock.patch.object(core, "retrieve_payloads", side_effect=failure):
                with self.assertRaises(core.SearchError) as raised:
                    pipeline.search("city", env)

            self.assertEqual(raised.exception.code, "provider_unavailable")
            self.assertEqual(os.readlink(layout.current), previous)
            events = [
                json.loads(line)
                for line in layout.diagnostics.read_text(encoding="utf-8").splitlines()
            ]
            stages = [event["stage"] for event in events]
            self.assertEqual(
                stages[-4:],
                ["REQUESTED", "PROVIDER_CONNECTING", "PROVIDER_RESPONSE", "FAILED"],
            )
            self.assertEqual(events[-2]["http_status"], 503)
            self.assertEqual(events[-1]["terminal_outcome"], "provider_unavailable")
            self.assertEqual(events[-1]["publication_status"], "not_published")
            self.assertTrue(events[-1]["authoritative"])

            current = (layout.online / previous).resolve()
            self.assertTrue(current.is_dir())
            failed_dirs = [
                item for item in layout.generations.iterdir()
                if item.resolve() != current
            ]
            self.assertEqual(failed_dirs, [])

    def test_preview_stage_failure_is_classified_and_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = base_env()
            env.update({
                "HOME": temp,
                "XDG_CACHE_HOME": str(Path(temp) / "cache"),
            })
            layout = core.CacheLayout.from_environment(env)
            previous = seed_generation(layout)
            candidate = make_candidate()
            with mock.patch.object(core, "retrieve_payloads", return_value={"relevance": {"data": []}}), \
                 mock.patch.object(core, "rank_payloads", return_value=[candidate]), \
                 mock.patch.object(pipeline, "download_ranked_previews", return_value=[]):
                with self.assertRaises(core.SearchError) as raised:
                    pipeline.search("city", env)

            self.assertEqual(raised.exception.code, "preview_failed")
            self.assertEqual(os.readlink(layout.current), previous)
            events = [
                json.loads(line)
                for line in layout.diagnostics.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(events[-1]["stage"], "FAILED")
            self.assertEqual(events[-1]["terminal_outcome"], "preview_failed")

    def test_stale_search_cannot_publish_and_is_logged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = base_env()
            env.update({
                "HOME": temp,
                "XDG_CACHE_HOME": str(Path(temp) / "cache"),
            })
            layout = core.CacheLayout.from_environment(env)
            previous = seed_generation(layout)
            candidate = make_candidate()
            with mock.patch.object(core, "retrieve_payloads", return_value={"relevance": {"data": []}}), \
                 mock.patch.object(core, "rank_payloads", return_value=[candidate]), \
                 mock.patch.object(
                     pipeline,
                     "download_ranked_previews",
                     side_effect=core.StaleRequest("stale"),
                 ):
                with self.assertRaises(core.StaleRequest):
                    pipeline.search("city", env)

            self.assertEqual(os.readlink(layout.current), previous)
            events = [
                json.loads(line)
                for line in layout.diagnostics.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(events[-1]["stage"], "STALE_REJECTED")
            self.assertFalse(events[-1]["authoritative"])
            self.assertEqual(events[-1]["publication_status"], "rejected")
    def test_diagnostic_log_rotates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = {
                "HOME": temp,
                "XDG_CACHE_HOME": str(Path(temp) / "cache"),
            }
            layout = core.CacheLayout.from_environment(env)
            with mock.patch.object(core, "DIAGNOSTIC_LOG_MAX_BYTES", 120):
                for index in range(8):
                    core.append_diagnostic(layout, {
                        "request_id": index,
                        "query": "x" * 40,
                        "provider": "wallhaven",
                        "stage": "FAILED",
                    })
            self.assertTrue(layout.diagnostics.exists())
            self.assertTrue(layout.diagnostics.with_suffix(".jsonl.1").exists())

    def test_doctor_is_read_only_for_request_and_current_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = {
                "HOME": temp,
                "XDG_CACHE_HOME": str(Path(temp) / "cache"),
            }
            layout = core.CacheLayout.from_environment(env)
            previous = seed_generation(layout)
            authority_before = layout.authority.read_text(encoding="utf-8")
            with mock.patch.object(
                core,
                "_doctor_provider",
                return_value=[("WARN", "Wallhaven API", "HTTP 503; provider temporarily unavailable")],
            ):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    rc = core.doctor(env)

            self.assertEqual(rc, 0)
            self.assertEqual(layout.authority.read_text(encoding="utf-8"), authority_before)
            self.assertEqual(os.readlink(layout.current), previous)
            rendered = output.getvalue()
            self.assertIn("PASS  current generation valid", rendered)
            self.assertIn("WARN  Wallhaven API", rendered)


if __name__ == "__main__":
    unittest.main()
