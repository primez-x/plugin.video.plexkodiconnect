import importlib
import importlib.util
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class DownloadRecorder(object):
    def __init__(self, pages):
        self.calls = []
        self.pages = list(pages)

    def downloadUrl(self, url, *args, **kwargs):
        self.calls.append((url, args, kwargs))
        return self.pages.pop(0)


def watchlist_page(total_size, count):
    page = ET.Element("MediaContainer", totalSize=str(total_size))
    for index in range(count):
        ET.SubElement(page, "Video", ratingKey=str(index))
    return page


def load_entrypoint():
    module_names = (
        "xbmc",
        "xbmcplugin",
        "xbmcgui",
        "resources.lib.entrypoint",
        "resources.lib.utils",
        "resources.lib.clientinfo",
        "resources.lib.path_ops",
        "resources.lib.downloadutils",
        "resources.lib.plex_api",
        "resources.lib.plex_functions",
        "resources.lib.variables",
        "resources.lib.app",
        "resources.lib.widgets",
        "resources.lib.library_sync",
        "resources.lib.library_sync.nodes",
    )
    for module_name in module_names:
        sys.modules.pop(module_name, None)

    xbmc = types.ModuleType("xbmc")
    sys.modules["xbmc"] = xbmc
    sys.modules["xbmcplugin"] = types.ModuleType("xbmcplugin")
    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.ListItem = object
    sys.modules["xbmcgui"] = xbmcgui

    modules = {}
    for module_name in module_names[4:]:
        modules[module_name] = types.ModuleType(module_name)
        sys.modules[module_name] = modules[module_name]

    modules["resources.lib.utils"].window = lambda key: "plex-token"
    modules["resources.lib.clientinfo"].getXArgsDeviceInfo = (
        lambda options=None, include_token=True: dict(options or {})
    )
    modules["resources.lib.downloadutils"].DownloadUtils = object
    modules["resources.lib.plex_api"].API = object
    modules["resources.lib.plex_api"].mass_api = lambda *args, **kwargs: []
    modules["resources.lib.library_sync.nodes"].NODE_TYPES = {}
    return importlib.import_module("resources.lib.entrypoint")


class WatchlistListingTests(unittest.TestCase):
    def test_downloads_and_merges_every_provider_page_at_its_100_item_limit(self):
        entrypoint = load_entrypoint()
        downloader = DownloadRecorder([watchlist_page(101, 100), watchlist_page(101, 1)])
        entrypoint.DU = lambda: downloader

        result = entrypoint._download_watchlist()

        self.assertEqual(len(result), 101)
        # Every page asks for the provider ids; paging stays in parameters
        self.assertEqual(
            [call[0] for call in downloader.calls],
            [entrypoint.WATCHLIST_URL] * 2,
        )
        self.assertIn("?includeGuids=1", entrypoint.WATCHLIST_URL)
        self.assertEqual(
            [call[2]["parameters"] for call in downloader.calls],
            [
                {"X-Plex-Container-Start": 0, "X-Plex-Container-Size": 100},
                {"X-Plex-Container-Start": 100, "X-Plex-Container-Size": 100},
            ],
        )
        self.assertEqual(
            [call[2]["timeout"] for call in downloader.calls],
            [entrypoint.DISCOVER_HTTP_TIMEOUT] * 2,
        )


class FakeAPI(object):
    def __init__(self, xml):
        self.xml = xml


def provider_item(parent, rating_key, guid):
    return ET.SubElement(
        parent, "Video", type="movie", ratingKey=rating_key, guid=guid
    )


class WatchlistApisTests(unittest.TestCase):
    def test_keeps_provider_only_items_and_substitutes_every_local_match(self):
        entrypoint = load_entrypoint()
        xml = ET.Element("MediaContainer")
        provider_item(xml, "aaa", "plex://movie/aaa")
        provider_item(xml, "bbb", "plex://movie/bbb")
        provider_item(xml, "ccc", "plex://movie/ccc")

        lookups = []

        def mass_api(items, check_by_guid=False, match_external_ids=False):
            lookups.append((check_by_guid, match_external_ids))
            if not check_by_guid:
                return [FakeAPI(child) for child in items]
            matches = []
            for child in items:
                if child.get("guid") == "plex://movie/bbb":
                    for plex_id in ("41", "42"):
                        local = ET.Element("Video", attrib=dict(child.attrib))
                        local.set("ratingKey", plex_id)
                        matches.append(FakeAPI(local))
            return matches

        entrypoint.mass_api = mass_api

        apis = entrypoint._watchlist_apis(xml)

        self.assertEqual(
            [api.xml.get("ratingKey") for api in apis],
            ["aaa", "41", "42", "ccc"],
        )
        # Legacy-agent library items are matched by TMDb/IMDb/TVDB id
        self.assertEqual(lookups, [(True, True), (False, False)])


    def test_entries_pair_each_library_match_with_its_provider_item(self):
        entrypoint = load_entrypoint()
        xml = ET.Element("MediaContainer")
        provider_item(xml, "aaa", "plex://movie/aaa")
        provider_item(xml, "bbb", "plex://movie/bbb")

        def mass_api(items, check_by_guid=False, match_external_ids=False):
            if not check_by_guid:
                return [FakeAPI(child) for child in items]
            matches = []
            for child in items:
                if child.get("guid") == "plex://movie/bbb":
                    for plex_id in ("41", "42"):
                        local = ET.Element("Video", attrib=dict(child.attrib))
                        local.set("ratingKey", plex_id)
                        matches.append(FakeAPI(local))
            return matches

        entrypoint.mass_api = mass_api

        entries = entrypoint._watchlist_entries(xml)

        self.assertEqual(
            [
                (
                    api.xml.get("ratingKey"),
                    None if provider is None else provider.get("ratingKey"),
                )
                for api, provider in entries
            ],
            [("aaa", None), ("41", "bbb"), ("42", "bbb")],
        )
        # The pair holds the provider element itself, not a copy
        self.assertIs(entries[1][1], xml[1])

def load_plex_api_package(items_by_guid):
    module_names = (
        "resources.lib.plex_api",
        "resources.lib.plex_api.base",
        "resources.lib.plex_api.artwork",
        "resources.lib.plex_api.file",
        "resources.lib.plex_api.media",
        "resources.lib.plex_api.user",
        "resources.lib.plex_api.playback",
        "resources.lib.plex_db",
    )
    for module_name in module_names:
        sys.modules.pop(module_name, None)

    class Base(object):
        def __init__(self, xml):
            self.xml = xml
            self._checked_db = False
            self.kodi_id = None

        @property
        def plex_id(self):
            return self.xml.get("ratingKey")

        def check_by_guid(self, plexdb=None):
            return items_by_guid.get(self.xml.get("guid"), [])

        def check_db(self, plexdb=None):
            if self._checked_db:
                return
            self._checked_db = True
            self.kodi_id = "kodi-%s" % self.plex_id

    base = types.ModuleType("resources.lib.plex_api.base")
    base.Base = Base
    sys.modules["resources.lib.plex_api.base"] = base
    for name, cls in (
        ("artwork", "Artwork"),
        ("file", "File"),
        ("media", "Media"),
        ("user", "User"),
        ("playback", "Playback"),
    ):
        module = types.ModuleType("resources.lib.plex_api.%s" % name)
        setattr(module, cls, type(cls, (object,), {}))
        sys.modules["resources.lib.plex_api.%s" % name] = module

    class PlexDB(object):
        def __init__(self, lock=False):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    plex_db = types.ModuleType("resources.lib.plex_db")
    plex_db.PlexDB = PlexDB
    sys.modules["resources.lib.plex_db"] = plex_db

    path = REPO_ROOT / "resources" / "lib" / "plex_api" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "resources.lib.plex_api", path,
        submodule_search_locations=[str(path.parent)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["resources.lib.plex_api"] = module
    spec.loader.exec_module(module)
    return module


class MassApiGuidTests(unittest.TestCase):
    def tearDown(self):
        for module_name in list(sys.modules):
            if module_name.startswith("resources.lib.plex_api") or \
                    module_name == "resources.lib.plex_db":
                sys.modules.pop(module_name, None)

    def test_each_guid_match_gets_its_own_api_and_kodi_lookup(self):
        plex_api = load_plex_api_package(
            {"plex://movie/x": [{"plex_id": 41}, {"plex_id": 42}]}
        )
        xml = ET.Element("MediaContainer")
        provider_item(xml, "x-provider", "plex://movie/x")
        provider_item(xml, "y-provider", "plex://movie/y")

        apis = plex_api.mass_api(xml, check_by_guid=True)

        self.assertEqual([api.plex_id for api in apis], ["41", "42"])
        self.assertIsNot(apis[0].xml, apis[1].xml)
        self.assertEqual([api.kodi_id for api in apis], ["kodi-41", "kodi-42"])
        self.assertEqual(
            [api.xml.get("key") for api in apis],
            ["/library/metadata/41", "/library/metadata/42"],
        )


if __name__ == "__main__":
    unittest.main()
