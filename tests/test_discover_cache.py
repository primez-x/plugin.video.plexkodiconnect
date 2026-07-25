import importlib
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_cache():
    sys.modules.pop("resources.lib.discover_cache", None)
    resources_lib = sys.modules.get("resources.lib")
    if resources_lib is not None and hasattr(resources_lib, "discover_cache"):
        delattr(resources_lib, "discover_cache")

    properties = {"plex_token": "account-one"}
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
    cache = importlib.import_module("resources.lib.discover_cache")
    return cache, properties


def provider_xml(title="Cached item"):
    root = ET.Element("MediaContainer")
    ET.SubElement(root, "Video", type="movie", title=title, ratingKey="item")
    return root


class DiscoverCacheTests(unittest.TestCase):
    URL = "https://discover.provider.plex.tv/hubs/sections/home/new-for-you"

    def test_returns_a_fresh_account_scoped_cached_xml_response(self):
        cache, properties = load_cache()
        cache.time = lambda: 100.0

        self.assertTrue(cache.put_xml(self.URL, [provider_xml()]))
        cache.time = lambda: 110.0
        cached = cache.get_xml(self.URL, 120)

        self.assertEqual(len(cached), 1)
        self.assertEqual(cached[0][0].get("title"), "Cached item")
        properties["plex_token"] = "account-two"
        self.assertIsNone(cache.get_xml(self.URL, 120))

    def test_expired_or_invalidated_entries_are_never_reused(self):
        cache, _ = load_cache()
        cache.time = lambda: 100.0
        self.assertTrue(cache.put_xml(self.URL, [provider_xml()]))

        cache.time = lambda: 221.0
        self.assertIsNone(cache.get_xml(self.URL, 120))
        cache.time = lambda: 222.0
        self.assertTrue(cache.put_xml(self.URL, [provider_xml()]))
        cache.invalidate()
        self.assertIsNone(cache.get_xml(self.URL, 120))

    def test_malformed_cached_data_is_cleared_and_treated_as_a_cache_miss(self):
        cache, properties = load_cache()
        cache.time = lambda: 100.0
        key = cache._cache_key(self.URL)
        payload_property = cache._payload_property(key)
        properties[payload_property] = "not-valid-base64"
        properties[cache._updated_property(key)] = "100.0"

        self.assertIsNone(cache.get_xml(self.URL, 120))
        self.assertNotIn(payload_property, properties)

    def test_refuses_an_unbounded_provider_payload(self):
        cache, _ = load_cache()
        oversized = provider_xml()
        oversized[0].set("summary", "x" * (cache.CACHE_MAX_RAW_BYTES + 1))

        self.assertFalse(cache.put_xml(self.URL, [oversized]))


if __name__ == "__main__":
    unittest.main()
