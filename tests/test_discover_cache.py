import importlib
import os
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_cache(profile, properties=None):
    module_names = (
        "resources.lib.discover_cache",
        "resources.lib.utils",
        "resources.lib.variables",
        "resources.lib.plex_discover",
    )
    resources_lib = sys.modules.get("resources.lib")
    if resources_lib is not None:
        for attribute in ("discover_cache", "utils", "variables", "plex_discover"):
            if hasattr(resources_lib, attribute):
                delattr(resources_lib, attribute)
    for module_name in module_names:
        sys.modules.pop(module_name, None)

    properties = {"plex_token": "account-one"} if properties is None else properties
    utils = types.ModuleType("resources.lib.utils")

    def window(key, value=None, clear=False):
        if clear:
            properties.pop(key, None)
        elif value is not None:
            properties[key] = value
        else:
            return properties.get(key, "")

    utils.window = window
    sys.modules["resources.lib.utils"] = utils

    variables = types.ModuleType("resources.lib.variables")
    variables.ADDON_PROFILE = profile
    sys.modules["resources.lib.variables"] = variables

    cache = importlib.import_module("resources.lib.discover_cache")
    return cache, properties


def provider_xml(*items):
    root = ET.Element("MediaContainer")
    for rating_key, title in items or (("item", "Cached item"),):
        ET.SubElement(
            root,
            "Video",
            type="movie",
            title=title,
            ratingKey=rating_key,
            guid="plex://movie/%s" % rating_key,
        )
    return root


class DiscoverCacheTests(unittest.TestCase):
    URL = "https://discover.provider.plex.tv/hubs/sections/home/new-for-you?includeMetadata=1&limit=20"
    DETAIL_URL = "https://metadata.provider.plex.tv/library/metadata/item"

    def test_persistent_record_survives_a_new_plugin_process_and_is_account_scoped(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, _ = load_cache(profile)
            cache.time = lambda: 100.0
            self.assertTrue(cache.put_xml(self.URL, [provider_xml()], "hub"))

            cache, properties = load_cache(profile)
            cache.time = lambda: 110.0
            freshness, cached = cache.read_xml(self.URL, "hub")

            self.assertEqual(freshness, cache.CACHE_FRESH)
            self.assertEqual(cached[0][0].get("title"), "Cached item")
            properties["plex_token"] = "account-two"
            self.assertEqual(cache.read_xml(self.URL, "hub"), (cache.CACHE_MISS, None))

    def test_stale_record_renders_immediately_and_queues_one_refresh(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, _ = load_cache(profile)
            cache.time = lambda: 100.0
            self.assertTrue(cache.put_xml(self.URL, [provider_xml()], "hub"))

            cache.time = lambda: 100.0 + cache.CACHE_POLICIES["hub"]["fresh"] + 1
            freshness, cached = cache.read_xml(self.URL, "hub")

            self.assertEqual(freshness, cache.CACHE_STALE)
            self.assertEqual(cached[0][0].get("ratingKey"), "item")
            self.assertTrue(cache.enqueue_refresh(self.URL, "hub"))
            self.assertTrue(cache.enqueue_refresh(self.URL, "hub"))
            request = cache.claim_refresh()
            self.assertEqual(request["url"], self.URL)
            self.assertEqual(request["kind"], "hub")
            self.assertTrue(cache.finish_refresh(request, True))
            self.assertIsNone(cache.claim_refresh())

    def test_foreground_write_only_requests_background_maintenance(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, properties = load_cache(profile)
            cache.time = lambda: 100.0
            cache._sweep_static_locked = lambda: self.fail(
                "foreground cache writes must not scan the durable cache"
            )

            self.assertTrue(cache.put_xml(self.URL, [provider_xml()], "hub"))
            self.assertEqual(
                properties[cache.CACHE_MAINTENANCE_REQUESTED],
                "100.0",
            )
            self.assertTrue(cache.maintenance_due())

    def test_source_current_check_never_decodes_the_cached_hub_per_tile(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, _ = load_cache(profile)
            cache.time = lambda: 100.0
            self.assertTrue(cache.put_xml(self.URL, [provider_xml()], "hub"))
            cache._decode_xmls = lambda value: self.fail(
                "source freshness must not decode the full provider response"
            )

            self.assertTrue(cache.source_is_current(self.URL))

    def test_expired_source_is_purged_and_does_not_render(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, _ = load_cache(profile)
            cache.time = lambda: 100.0
            self.assertTrue(cache.put_xml(self.URL, [provider_xml()], "hub"))
            source_path = cache._source_path(self.URL)

            cache.time = lambda: 100.0 + cache.CACHE_RETENTION_SECONDS + 1
            self.assertEqual(cache.read_xml(self.URL, "hub"), (cache.CACHE_MISS, None))
            cache.sweep()
            self.assertFalse(Path(source_path).exists())

    def test_seen_source_extends_retention_without_preserving_unseen_content(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, _ = load_cache(profile)
            cache.time = lambda: 100.0
            second_url = self.URL.replace("new-for-you", "top-watchlisted")
            self.assertTrue(cache.put_xml(self.URL, [provider_xml(("seen", "Seen"))], "hub"))
            self.assertTrue(
                cache.put_xml(second_url, [provider_xml(("unseen", "Unseen"))], "hub")
            )
            seen_path = cache._source_path(self.URL)
            unseen_path = cache._source_path(second_url)

            cache.time = lambda: 100.0 + 13 * 24 * 60 * 60
            self.assertEqual(
                cache.read_xml(self.URL, "hub")[0],
                cache.CACHE_STALE,
            )
            cache.time = lambda: 100.0 + cache.CACHE_RETENTION_SECONDS + 1
            cache.sweep()

            self.assertTrue(Path(seen_path).exists())
            self.assertFalse(Path(unseen_path).exists())
            self.assertEqual(cache.read_xml(self.URL, "hub")[0], cache.CACHE_STALE)

    def test_corrupt_record_is_isolated_and_never_replaces_an_unrelated_item(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, _ = load_cache(profile)
            cache.time = lambda: 100.0
            second_url = self.URL.replace("new-for-you", "top-watchlisted")
            self.assertTrue(cache.put_xml(self.URL, [provider_xml(("one", "One"))], "hub"))
            self.assertTrue(
                cache.put_xml(second_url, [provider_xml(("two", "Two"))], "hub")
            )
            Path(cache._source_path(self.URL)).write_text("not json", encoding="utf-8")

            cache.invalidate()
            self.assertEqual(cache.read_xml(self.URL, "hub"), (cache.CACHE_MISS, None))
            freshness, cached = cache.read_xml(second_url, "hub")
            self.assertEqual(freshness, cache.CACHE_FRESH)
            self.assertEqual(cached[0][0].get("ratingKey"), "two")

    def test_watchlist_overlay_expires_before_static_detail_metadata(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, _ = load_cache(profile)
            cache.time = lambda: 100.0
            self.assertTrue(cache.put_xml(self.DETAIL_URL, [provider_xml()], "detail"))
            self.assertTrue(cache.record_watchlist_state("item", True))

            cache.time = lambda: 101.0
            self.assertEqual(cache.watchlist_hint("item", "absent"), "present")
            cache.time = lambda: 100.0 + cache.WATCHLIST_OVERLAY_SECONDS + 1
            self.assertEqual(cache.watchlist_hint("item", "absent"), "absent")
            self.assertEqual(cache.read_xml(self.DETAIL_URL, "detail")[0], cache.CACHE_FRESH)

    def test_watchlist_overlay_is_not_shared_when_the_plex_account_changes(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, properties = load_cache(profile)
            cache.time = lambda: 100.0
            self.assertTrue(cache.record_watchlist_state("item", True))

            cache.time = lambda: 101.0
            self.assertEqual(cache.watchlist_hint("item", "absent"), "present")
            properties["plex_token"] = "account-two"
            self.assertEqual(cache.watchlist_hint("item", "absent"), "absent")

    def test_claimed_refresh_can_receive_one_dirty_requeue(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, _ = load_cache(profile)
            cache.time = lambda: 100.0
            self.assertTrue(cache.enqueue_refresh(self.URL, "hub"))
            first = cache.claim_refresh()
            self.assertIsNotNone(first)

            self.assertTrue(cache.enqueue_refresh(self.URL, "hub"))
            self.assertTrue(cache.finish_refresh(first, True))
            second = cache.claim_refresh()

            self.assertIsNotNone(second)
            self.assertNotEqual(first["_claim_path"], second["_claim_path"])
            self.assertTrue(cache.finish_refresh(second, True))

    def test_foreground_refreshes_take_priority_over_catalog_prefetches(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, _ = load_cache(profile)
            cache.time = lambda: 100.0
            prefetch_url = self.URL
            foreground_url = self.URL.replace("new-for-you", "top-watchlisted")
            self.assertTrue(
                cache.enqueue_refresh(
                    prefetch_url,
                    "hub",
                    priority=cache.REFRESH_PRIORITY_PREFETCH,
                )
            )
            self.assertTrue(cache.enqueue_refresh(foreground_url, "hub"))

            first = cache.claim_refresh()

            self.assertEqual(first["url"], foreground_url)
            self.assertTrue(cache.finish_refresh(first, True))

    def test_catalog_prefetch_is_account_scoped_and_does_not_write_foreground_files(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, properties = load_cache(profile)

            self.assertTrue(cache.request_hub_prefetch([self.URL]))
            self.assertFalse(Path(cache._directory("refresh")).exists())
            self.assertEqual(cache.claim_hub_prefetch(), (self.URL,))
            self.assertEqual(cache.claim_hub_prefetch(), ())

            self.assertTrue(cache.request_hub_prefetch([self.URL]))
            properties["plex_token"] = "account-two"
            self.assertEqual(cache.claim_hub_prefetch(), ())

    def test_failed_refresh_is_retried_and_active_claims_are_lease_protected(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, _ = load_cache(profile)
            cache.time = lambda: 100.0
            self.assertTrue(cache.enqueue_refresh(self.URL, "hub"))
            request = cache.claim_refresh()
            self.assertIsNotNone(request)
            self.assertEqual(cache.recover_refresh_claims(), 0)
            self.assertTrue(cache.finish_refresh(request, False))

            queued = cache._read_json(cache._refresh_path(self.URL, "hub"))
            self.assertEqual(queued["attempt"], 1)
            cache.time = lambda: queued["not_before"]
            retry = cache.claim_refresh()
            self.assertIsNotNone(retry)
            self.assertTrue(cache.finish_refresh(retry, True))

    def test_claim_refresh_recovers_an_expired_lease_without_a_restart(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, _ = load_cache(profile)
            cache.time = lambda: 100.0
            self.assertTrue(cache.enqueue_refresh(self.URL, "hub"))
            claimed = cache.claim_refresh()
            self.assertIsNotNone(claimed)

            cache.time = lambda: 100.0 + cache.REFRESH_LEASE_SECONDS + 1
            recovered = cache.claim_refresh()

            self.assertIsNotNone(recovered)
            self.assertTrue(cache.finish_refresh(recovered, True))

    def test_sweep_marks_maintenance_complete_only_after_the_background_scan(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, _ = load_cache(profile)
            cache.time = lambda: 100.0
            self.assertTrue(cache.put_xml(self.URL, [provider_xml()], "hub"))
            cache._sweep_static_locked = lambda: 3

            self.assertEqual(cache.sweep(), 3)
            self.assertFalse(cache.maintenance_due())

    @unittest.skipIf(
        os.name == "nt",
        "Windows cannot replace an open lock file while CoreELEC can",
    )
    def test_replaced_stale_lock_is_not_removed_by_its_old_owner(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, _ = load_cache(profile)
            cache.time = lambda: 100.0
            lock_root = str(Path(profile) / "locks")
            lock_path = Path(lock_root) / "test.lock"
            first = cache._lock(lock_root, "test")
            self.assertTrue(first.__enter__())
            os.utime(lock_path, (0, 0))
            cache.time = lambda: cache.CACHE_LOCK_STALE_SECONDS + 1
            second = cache._lock(lock_root, "test")
            self.assertTrue(second.__enter__())

            first.__exit__(None, None, None)
            self.assertTrue(lock_path.exists())
            second.__exit__(None, None, None)
            self.assertFalse(lock_path.exists())

    def test_failed_retry_write_keeps_the_only_refresh_claim(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, _ = load_cache(profile)
            cache.time = lambda: 100.0
            self.assertTrue(cache.enqueue_refresh(self.URL, "hub"))
            request = cache.claim_refresh()
            self.assertIsNotNone(request)
            original_write = cache._write_json
            cache._write_json = lambda *args, **kwargs: False
            try:
                self.assertFalse(cache.finish_refresh(request, False))
            finally:
                cache._write_json = original_write

            self.assertTrue(Path(request["_claim_path"]).exists())

    def test_refuses_an_unbounded_provider_payload(self):
        with tempfile.TemporaryDirectory() as profile:
            cache, _ = load_cache(profile)
            oversized = provider_xml()
            oversized[0].set("summary", "x" * (cache.CACHE_MAX_RAW_BYTES + 1))

            self.assertFalse(cache.put_xml(self.URL, [oversized], "hub"))


if __name__ == "__main__":
    unittest.main()
