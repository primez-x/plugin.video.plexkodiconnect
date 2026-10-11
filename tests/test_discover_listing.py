import importlib
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlencode


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_entrypoint():
    module_names = (
        "xbmc",
        "xbmcgui",
        "xbmcplugin",
        "resources.lib.entrypoint",
        "resources.lib.discover_cache",
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
    resources_lib = sys.modules.get("resources.lib")
    if resources_lib is not None:
        for attribute in (
            "entrypoint",
            "discover_cache",
            "watchlist",
            "utils",
            "clientinfo",
            "path_ops",
            "downloadutils",
            "plex_api",
            "plex_functions",
            "variables",
            "app",
            "widgets",
            "library_sync",
        ):
            if hasattr(resources_lib, attribute):
                delattr(resources_lib, attribute)
    for module_name in module_names:
        sys.modules.pop(module_name, None)
    sys.modules.pop("resources.lib.watchlist", None)

    xbmc = types.ModuleType("xbmc")
    xbmc.sleep = lambda milliseconds: None
    sys.modules["xbmc"] = xbmc

    dialog_calls = []
    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.ListItem = object

    class Dialog(object):
        def info(self, listitem):
            dialog_calls.append(listitem)

    xbmcgui.Dialog = Dialog
    sys.modules["xbmcgui"] = xbmcgui

    directory_calls = []
    xbmcplugin = types.ModuleType("xbmcplugin")
    xbmcplugin.SORT_METHOD_UNSORTED = 0
    xbmcplugin.setContent = lambda *args, **kwargs: None
    xbmcplugin.addDirectoryItems = lambda *args, **kwargs: directory_calls.append(args)
    xbmcplugin.addSortMethod = lambda *args, **kwargs: None
    sys.modules["xbmcplugin"] = xbmcplugin

    modules = {}
    for module_name in module_names[4:]:
        modules[module_name] = types.ModuleType(module_name)
        sys.modules[module_name] = modules[module_name]

    utils = modules["resources.lib.utils"]
    utils.window = lambda key: {
        "plex_authenticated": "true",
        "plex_token": "token",
        "plex_restricteduser": "",
    }.get(key, "")
    utils.settings = lambda key: "0"
    utils.extend_url = lambda url, params: "%s?%s" % (url, urlencode(params))

    modules["resources.lib.clientinfo"].getXArgsDeviceInfo = (
        lambda options=None, include_token=True: dict(options or {})
    )
    modules["resources.lib.downloadutils"].DownloadUtils = object
    modules["resources.lib.app"].init = lambda entrypoint=False: None

    variables = modules["resources.lib.variables"]
    variables.ADDON_ID = "plugin.video.plexkodiconnect"
    variables.CONTENT_TYPE_FILE = "files"
    variables.CONTENT_FROM_PLEX_TYPE = {"movie": "movies", "show": "tvshows"}

    cache = modules["resources.lib.discover_cache"]
    cache.CACHE_FRESH = "fresh"
    cache.CACHE_STALE = "stale"
    cache.read_xml = lambda *args, **kwargs: ("miss", None)
    cache.put_xml = lambda *args, **kwargs: True
    cache.enqueue_refresh = lambda *args, **kwargs: True
    cache.source_is_current = lambda *args, **kwargs: True
    cache.watchlist_hint = lambda rating_key, fallback=None: fallback

    class API(object):
        def __init__(self, xml):
            self.xml = xml
            self.plex_type = xml.get("type")

    mass_api_calls = []
    external_id_calls = []

    def mass_api(xml, check_by_guid=False, match_external_ids=False):
        mass_api_calls.append((list(xml), check_by_guid))
        external_id_calls.append(match_external_ids)
        if check_by_guid:
            return []
        return [API(child) for child in xml]

    mass_api.external_id_calls = external_id_calls
    modules["resources.lib.plex_api"].API = API
    modules["resources.lib.plex_api"].mass_api = mass_api
    modules["resources.lib.library_sync.nodes"].NODE_TYPES = {}

    widgets = modules["resources.lib.widgets"]
    widgets.generate_item = lambda api: {
        "title": api.xml.get("title"),
        "label": api.xml.get("title"),
        "type": api.plex_type,
        "file": "plugin://plugin.video.plexkodiconnect?mode=play",
        "extraproperties": {},
    }
    widgets.prepare_listitem = lambda item: item
    widgets.create_listitem = lambda item, as_tuple=True, **kwargs: (
        (item["file"], item, item.get("isFolder", False)) if as_tuple else item
    )

    entrypoint = importlib.import_module("resources.lib.entrypoint")
    entrypoint.sys.argv = ["plugin://plugin.video.plexkodiconnect", "1", ""]
    return entrypoint, directory_calls, dialog_calls, mass_api_calls


def provider_metadata(rating_key="abc123", user_state=None):
    root = ET.Element("MediaContainer")
    attributes = {
        "type": "movie",
        "title": "Provider Movie",
        "ratingKey": rating_key,
        "guid": "plex://movie/%s" % rating_key,
    }
    if user_state is not None:
        attributes["userState"] = str(user_state)
    ET.SubElement(root, "Video", **attributes)
    return root


class DiscoverListingTests(unittest.TestCase):
    def test_provider_cache_returns_a_fresh_listing_without_a_network_request(self):
        entrypoint, _, _, _ = load_entrypoint()
        cached = [provider_metadata("cached")]
        calls = []

        class Cache(object):
            CACHE_STALE = "stale"

            @staticmethod
            def read_xml(url, cache_kind, item_key=None):
                calls.append(("read", url, cache_kind, item_key))
                return "fresh", cached

            @staticmethod
            def put_xml(url, xmls, cache_kind):
                raise AssertionError("a cache hit must not be written or fetched")

            @staticmethod
            def enqueue_refresh(url, cache_kind):
                raise AssertionError("a fresh cache hit must not enqueue a refresh")

        entrypoint.discover_cache = Cache()

        xmls = entrypoint._provider_xmls(
            "https://discover.provider.plex.tv/hubs/sections/home/new-for-you",
            ("discover.provider.plex.tv",),
            "hub",
        )

        self.assertEqual(xmls, cached)
        self.assertEqual(calls[0][0], "read")
        self.assertEqual(calls[0][2], "hub")

    def test_provider_cache_miss_falls_back_to_network_then_stores_response(self):
        entrypoint, _, _, _ = load_entrypoint()
        stored = []

        class Cache(object):
            CACHE_STALE = "stale"

            @staticmethod
            def read_xml(url, cache_kind, item_key=None):
                return "miss", None

            @staticmethod
            def put_xml(url, xmls, cache_kind):
                stored.append((url, list(xmls), cache_kind))
                return True

            @staticmethod
            def enqueue_refresh(url, cache_kind):
                raise AssertionError("a cold cache miss must fetch immediately")

        class Response(object):
            status_code = 200
            content = ET.tostring(provider_metadata("network"))
            headers = {}

        class Downloader(object):
            calls = []

            def downloadUrl(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Response()

        downloader = Downloader()
        entrypoint.discover_cache = Cache()
        entrypoint.DU = lambda: downloader

        xmls = entrypoint._provider_xmls(
            "https://discover.provider.plex.tv/hubs/sections/home/new-for-you",
            ("discover.provider.plex.tv",),
            "hub",
        )

        self.assertEqual(len(downloader.calls), 1)
        self.assertEqual(xmls[0][0].get("ratingKey"), "network")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0][1][0][0].get("ratingKey"), "network")
        self.assertEqual(stored[0][2], "hub")

    def test_provider_stale_cache_renders_without_foreground_network_io(self):
        entrypoint, _, _, _ = load_entrypoint()
        cached = [provider_metadata("stale")]
        refreshes = []

        class Cache(object):
            CACHE_STALE = "stale"

            @staticmethod
            def read_xml(url, cache_kind, item_key=None):
                return "stale", cached

            @staticmethod
            def enqueue_refresh(url, cache_kind):
                refreshes.append((url, cache_kind))
                return True

            @staticmethod
            def put_xml(url, xmls, cache_kind):
                raise AssertionError("a stale cache must not block on a foreground write")

        entrypoint.discover_cache = Cache()
        entrypoint.DU = lambda: self.fail("stale cache must not call Plex in the UI")

        xmls = entrypoint._provider_xmls(
            "https://discover.provider.plex.tv/hubs/sections/home/new-for-you",
            ("discover.provider.plex.tv",),
            "hub",
        )

        self.assertEqual(xmls, cached)
        self.assertEqual(
            refreshes,
            [
                (
                    "https://discover.provider.plex.tv/hubs/sections/home/new-for-you",
                    "hub",
                )
            ],
        )

    def test_provider_redirect_uses_one_bounded_batch_for_widget_latency(self):
        entrypoint, _, _, _ = load_entrypoint()
        services = ["service-%s" % index for index in range(41)]
        location = (
            "https://discover.provider.plex.tv/hubs/sections/home/new-for-you?%s"
            % (
                urlencode(
                    [
                        ("x-plex-preferred-services[]", service)
                        for service in services
                    ]
                    + [("other", "value")]
                )
            )
        )

        class Response(object):
            def __init__(self, status_code, content=b"", headers=None):
                self.status_code = status_code
                self.content = content
                self.headers = headers or {}

        class Downloader(object):
            def __init__(self):
                self.calls = []
                self.responses = [
                    Response(302, headers={"Location": location}),
                    Response(200, ET.tostring(provider_metadata())),
                ]

            def downloadUrl(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return self.responses.pop(0)

        downloader = Downloader()
        entrypoint.DU = lambda: downloader

        xmls = entrypoint._provider_xmls(
            "https://discover.provider.plex.tv/hubs/sections/home/new-for-you",
            ("discover.provider.plex.tv",),
        )

        self.assertEqual(len(xmls), 1)
        self.assertEqual(len(downloader.calls), 2)
        redirected_url, kwargs = downloader.calls[1]
        self.assertEqual(
            redirected_url.count("x-plex-preferred-services%5B%5D="), 20
        )
        self.assertFalse(kwargs["allow_redirects"])

    def _watchlist_with_cache(self, freshness, cached):
        entrypoint, _, _, _ = load_entrypoint()
        calls = {"download": 0, "read": [], "put": [], "enqueue": [], "listing": []}

        def read_xml(url, kind):
            calls["read"].append((url, kind))
            return freshness, cached

        entrypoint.discover_cache.read_xml = read_xml
        entrypoint.discover_cache.watchlist_generation = lambda: "generation"
        entrypoint.discover_cache.put_xml = (
            lambda url, xmls, kind, generation=None: calls["put"].append(
                (url, xmls, kind, generation)
            )
            or True
        )
        entrypoint.discover_cache.enqueue_refresh = (
            lambda url, kind: calls["enqueue"].append((url, kind)) or True
        )
        downloaded = provider_metadata("downloaded")

        def download():
            calls["download"] += 1
            return downloaded

        entrypoint._download_watchlist = download
        entrypoint._wait_for_auth = lambda: None
        entrypoint.show_listing = lambda xml, *args: calls["listing"].append(xml)
        entrypoint.watchlist()
        return entrypoint, calls, downloaded

    def test_watchlist_fresh_cache_lists_without_network(self):
        cached = provider_metadata("cached")
        _, calls, _ = self._watchlist_with_cache("fresh", [cached])

        self.assertEqual(calls["download"], 0)
        self.assertEqual(calls["enqueue"], [])
        self.assertEqual(calls["listing"], [cached])

    def test_watchlist_stale_cache_lists_and_revalidates_in_background(self):
        cached = provider_metadata("cached")
        entrypoint, calls, _ = self._watchlist_with_cache("stale", [cached])

        self.assertEqual(calls["download"], 0)
        self.assertEqual(calls["enqueue"], [(entrypoint.WATCHLIST_URL, "watchlist")])
        self.assertEqual(calls["listing"], [cached])

    def test_watchlist_cache_miss_downloads_and_stores(self):
        entrypoint, calls, downloaded = self._watchlist_with_cache("miss", None)

        self.assertEqual(calls["download"], 1)
        self.assertEqual(
            calls["put"],
            [(entrypoint.WATCHLIST_URL, [downloaded], "watchlist", "generation")],
        )
        self.assertEqual(calls["listing"], [downloaded])

    def test_watchlist_listing_caches_the_one_url_that_requests_provider_ids(self):
        entrypoint, calls, downloaded = self._watchlist_with_cache("miss", None)

        url = entrypoint.WATCHLIST_URL
        # discover_cache, its membership hints and the service warm-up use
        # the same plex_discover constant
        self.assertIs(url, entrypoint.plex_discover.WATCHLIST_URL)
        self.assertEqual(
            url,
            "https://discover.provider.plex.tv/library/sections/watchlist/all"
            "?includeGuids=1",
        )
        self.assertEqual(calls["read"], [(url, "watchlist")])
        self.assertEqual(calls["put"], [(url, [downloaded], "watchlist", "generation")])

    def _watchlist_listing(self, xml):
        """Render the cached Watchlist through the real show_listing."""
        entrypoint, directory_calls, _, _ = load_entrypoint()
        entrypoint.discover_cache.read_xml = lambda url, kind: ("fresh", [xml])
        entrypoint.v.PLEX_TYPE_VIDEO = "video"
        entrypoint.v.PLEX_TYPE_EPISODE = "episode"
        entrypoint.v.CONTENT_FROM_PLEX_TYPE["video"] = "videos"
        entrypoint.widgets.prepare_listitem = lambda item, key=None: item
        lookups = []

        class WatchlistAPI(object):
            tag = "Video"
            fast_key = None

            def __init__(self, xml, kodi_id=None):
                self.xml = xml
                self.plex_type = xml.get("type")
                self.kodi_id = kodi_id

        def mass_api(metadata, check_by_guid=False, match_external_ids=False):
            lookups.append((check_by_guid, match_external_ids))
            if not check_by_guid:
                return [WatchlistAPI(child) for child in metadata]
            matches = []
            for child in metadata:
                # A legacy-agent library item has no plex:// guid; only its
                # TMDb id matches. mass_api keeps the provider guid.
                if match_external_ids and child.find("Guid[@id='tmdb://603']") is not None:
                    child.set("ratingKey", "42")
                    child.set("key", "/library/metadata/42")
                    matches.append(WatchlistAPI(child, kodi_id=42))
            return matches

        entrypoint.API = WatchlistAPI
        entrypoint.mass_api = mass_api
        entrypoint.widgets.generate_item = lambda api: {
            "title": api.xml.get("title"),
            "label": api.xml.get("title"),
            "type": api.plex_type,
            "file": (
                "plugin://plugin.video.plexkodiconnect.movies/?plex_id=%s&mode=play"
                % api.xml.get("ratingKey")
            ),
            "extraproperties": {"DBID": str(api.kodi_id)} if api.kodi_id else {},
        }

        entrypoint.watchlist()

        self.assertEqual(len(directory_calls), 1)
        return directory_calls[0][1], lookups

    def test_watchlist_external_id_match_builds_the_native_library_tile(self):
        xml = provider_metadata()
        ET.SubElement(xml[0], "Guid", id="imdb://tt0133093")
        ET.SubElement(xml[0], "Guid", id="tmdb://603")
        ET.SubElement(
            xml,
            "Video",
            type="movie",
            title="Provider Only",
            ratingKey="def456",
            guid="plex://movie/def456",
        )

        items, lookups = self._watchlist_listing(xml)

        # Only the library lookup falls back to TMDb/IMDb/TVDB ids
        self.assertEqual(lookups, [(True, True), (False, False)])
        self.assertEqual(len(items), 2)
        path, item, is_folder = items[0]
        self.assertEqual(
            path,
            "plugin://plugin.video.plexkodiconnect.movies/?plex_id=42&mode=play",
        )
        self.assertFalse(is_folder)
        self.assertNotEqual(item.get("IsPlayable"), "false")
        self.assertEqual(item["extraproperties"]["DBID"], "42")

        # An unmatched item stays provider-only behind its provider key
        path, item, is_folder = items[1]
        self.assertIn("mode=discover_detail", path)
        self.assertIn("rating_key=def456", path)
        self.assertFalse(is_folder)
        self.assertEqual(item["IsPlayable"], "false")
        self.assertEqual(item["extraproperties"]["ratingKey"], "def456")
        self.assertNotIn("DBID", item["extraproperties"])

    def test_watchlist_without_a_library_match_stays_provider_only(self):
        xml = provider_metadata()
        ET.SubElement(xml[0], "Guid", id="tmdb://999")

        items, lookups = self._watchlist_listing(xml)

        self.assertEqual(lookups, [(True, True), (False, False)])
        self.assertEqual(len(items), 1)
        path, item, _ = items[0]
        self.assertIn("mode=discover_detail", path)
        self.assertIn("rating_key=abc123", path)
        self.assertEqual(item["IsPlayable"], "false")
        self.assertEqual(item["extraproperties"]["ratingKey"], "abc123")
        self.assertNotIn("DBID", item["extraproperties"])

    def test_hub_items_use_bulk_database_lookup_and_direct_detail_routes(self):
        entrypoint, directory_calls, _, mass_api_calls = load_entrypoint()
        xml = provider_metadata()
        entrypoint._provider_xmls = lambda *args, **kwargs: [xml]

        entrypoint.discover_hub("new-for-you")

        self.assertEqual(
            [check_by_guid for _, check_by_guid in mass_api_calls],
            [True, False],
        )
        self.assertEqual(len(directory_calls), 1)
        _, items, _ = directory_calls[0]
        path, item, is_folder = items[0]
        self.assertIn("mode=discover_detail", path)
        self.assertIn("rating_key=abc123", path)
        self.assertFalse(is_folder)
        self.assertEqual(item["IsPlayable"], "false")
        self.assertEqual(item["extraproperties"]["ratingKey"], "abc123")
        self.assertEqual(item["extraproperties"]["PlexDiscoverDetail"], "true")

    def test_hub_and_detail_publish_the_provider_watchlist_hint(self):
        entrypoint, directory_calls, dialog_calls, _ = load_entrypoint()
        xml = provider_metadata(user_state=0)
        entrypoint._provider_xmls = lambda *args, **kwargs: [xml]

        entrypoint.discover_hub("new-for-you")
        _, items, _ = directory_calls[0]
        self.assertEqual(
            items[0][1]["extraproperties"]["PlexWatchlistHint"], "absent"
        )

        self.assertTrue(entrypoint.discover_detail("abc123"))
        self.assertEqual(
            dialog_calls[0]["extraproperties"]["PlexWatchlistHint"], "absent"
        )

    def test_hub_filters_trailer_clips_before_detail_or_watchlist_actions(self):
        entrypoint, directory_calls, _, mass_api_calls = load_entrypoint()
        xml = provider_metadata()
        ET.SubElement(
            xml,
            "Video",
            type="clip",
            title="Provider Trailer",
            ratingKey="clip123",
            guid="plex://clip/clip123",
        )
        entrypoint._provider_xmls = lambda *args, **kwargs: [xml]

        entrypoint.discover_hub("new-for-you")

        self.assertEqual(
            [check_by_guid for _, check_by_guid in mass_api_calls],
            [True, False],
        )
        _, items, _ = directory_calls[0]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][1]["extraproperties"]["ratingKey"], "abc123")

    def test_hub_prefers_an_exact_guid_library_match_for_native_playback(self):
        entrypoint, directory_calls, _, _ = load_entrypoint()
        xml = provider_metadata()
        entrypoint._provider_xmls = lambda *args, **kwargs: [xml]

        class LocalAPI(object):
            plex_type = "movie"

            def __init__(self):
                self.xml = ET.Element(
                    "Video",
                    type="movie",
                    title="Provider Movie",
                    ratingKey="42",
                    guid="plex://movie/abc123",
                )

        local_api = LocalAPI()
        lookup_calls = []

        def mass_api(metadata, check_by_guid=False, match_external_ids=False):
            lookup_calls.append(check_by_guid)
            if check_by_guid:
                return [local_api]
            return [entrypoint.API(child) for child in metadata]

        entrypoint.mass_api = mass_api
        entrypoint.widgets.generate_item = lambda api: {
            "title": api.xml.get("title"),
            "label": api.xml.get("title"),
            "type": api.plex_type,
            "movieid": 42,
            "file": "plugin://plugin.video.plexkodiconnect.movies/?plex_id=42&mode=play",
            "extraproperties": {"DBID": "42"},
        }

        entrypoint.discover_hub("new-for-you")

        self.assertEqual(lookup_calls, [True, False])
        _, items, _ = directory_calls[0]
        path, item, is_folder = items[0]
        self.assertEqual(
            path,
            "plugin://plugin.video.plexkodiconnect.movies/?plex_id=42&mode=play",
        )
        self.assertFalse(is_folder)
        self.assertNotEqual(item.get("IsPlayable"), "false")
        self.assertEqual(item["extraproperties"]["DBID"], "42")
        self.assertEqual(item["extraproperties"]["ratingKey"], "abc123")

    def test_detail_rejects_a_trailer_clip_without_building_a_dead_play_item(self):
        entrypoint, _, dialog_calls, mass_api_calls = load_entrypoint()
        xml = provider_metadata("clip123")
        xml[0].set("type", "clip")
        xml[0].set("guid", "plex://clip/clip123")
        entrypoint._provider_xmls = lambda *args, **kwargs: [xml]

        self.assertFalse(entrypoint.discover_detail("clip123"))
        self.assertEqual(mass_api_calls, [])
        self.assertEqual(dialog_calls, [])

    def test_detail_uses_exact_provider_key_and_opens_native_info(self):
        entrypoint, _, dialog_calls, mass_api_calls = load_entrypoint()
        xml = provider_metadata()
        entrypoint._provider_xmls = lambda *args, **kwargs: [xml]

        self.assertTrue(entrypoint.discover_detail("abc123"))

        self.assertEqual(
            mass_api_calls,
            [([xml[0]], True), ([xml[0]], False)],
        )
        self.assertEqual(len(dialog_calls), 1)
        item = dialog_calls[0]
        self.assertEqual(item["file"], "")
        self.assertEqual(item["IsPlayable"], "false")
        self.assertEqual(item["extraproperties"]["ratingKey"], "abc123")
        self.assertEqual(
            item["extraproperties"]["plexguid"], "plex://movie/abc123"
        )

    def test_detail_prefers_an_exact_guid_library_match_for_playback(self):
        entrypoint, _, dialog_calls, _ = load_entrypoint()
        xml = provider_metadata()
        entrypoint._provider_xmls = lambda *args, **kwargs: [xml]

        class LocalAPI(object):
            plex_type = "movie"

            def __init__(self):
                self.xml = ET.Element(
                    "Video",
                    type="movie",
                    title="Provider Movie",
                    ratingKey="42",
                    guid="plex://movie/abc123",
                )

        local_api = LocalAPI()
        lookup_calls = []

        def mass_api(metadata, check_by_guid=False, match_external_ids=False):
            lookup_calls.append(check_by_guid)
            if check_by_guid:
                return [local_api]
            self.fail("a GUID-resolved local item must not fall back to provider-only")

        entrypoint.mass_api = mass_api
        entrypoint.widgets.generate_item = lambda api: {
            "title": api.xml.get("title"),
            "label": api.xml.get("title"),
            "type": api.plex_type,
            "movieid": 42,
            "file": "plugin://plugin.video.plexkodiconnect.movies/?plex_id=42&mode=play",
            "extraproperties": {"DBID": "42"},
        }

        self.assertTrue(entrypoint.discover_detail("abc123"))

        self.assertEqual(lookup_calls, [True])
        self.assertEqual(len(dialog_calls), 1)
        item = dialog_calls[0]
        self.assertEqual(
            item["file"],
            "plugin://plugin.video.plexkodiconnect.movies/?plex_id=42&mode=play",
        )
        self.assertNotEqual(item.get("IsPlayable"), "false")
        self.assertEqual(item["extraproperties"]["DBID"], "42")
        self.assertEqual(item["extraproperties"]["ratingKey"], "abc123")

    def test_hub_and_detail_request_provider_guids_for_the_library_match(self):
        entrypoint, _, _, mass_api_calls = load_entrypoint()
        xml = provider_metadata()
        urls = []

        def provider_xmls(url, *args, **kwargs):
            urls.append(url)
            return [xml]

        entrypoint._provider_xmls = provider_xmls

        entrypoint.discover_hub("new-for-you")
        self.assertTrue(entrypoint.discover_detail("abc123"))

        self.assertIn("includeGuids=1", urls[0])
        self.assertIn("includeMetadata=1", urls[0])
        self.assertEqual(
            urls[1],
            "https://metadata.provider.plex.tv/library/metadata/abc123"
            "?includeGuids=1",
        )
        # Only the library lookups fall back to TMDb/IMDb/TVDB ids
        self.assertEqual(
            [
                (check_by_guid, match_external_ids)
                for (_, check_by_guid), match_external_ids in zip(
                    mass_api_calls, entrypoint.mass_api.external_id_calls
                )
            ],
            [(True, True), (False, False), (True, True), (False, False)],
        )

    def _legacy_agent_library_match(self, entrypoint):
        """A synced item whose PMS guid is com.plexapp.agents.imdb://..."""

        class LocalAPI(object):
            plex_type = "movie"

            def __init__(self, xml):
                self.xml = xml

        def mass_api(metadata, check_by_guid=False, match_external_ids=False):
            if not check_by_guid:
                return [entrypoint.API(child) for child in metadata]
            if not match_external_ids:
                return []
            # mass_api keeps the provider guid and sets the local ratingKey
            local_xml = metadata[0]
            local_xml.set("ratingKey", "42")
            local_xml.set("key", "/library/metadata/42")
            return [LocalAPI(local_xml)]

        entrypoint.mass_api = mass_api
        entrypoint.widgets.generate_item = lambda api: {
            "title": api.xml.get("title"),
            "label": api.xml.get("title"),
            "type": api.plex_type,
            "movieid": 42,
            "file": (
                "plugin://plugin.video.plexkodiconnect.movies/?plex_id=%s&mode=play"
                % api.xml.get("ratingKey")
            ),
            "extraproperties": {"DBID": "42"}
            if api.xml.get("ratingKey") == "42"
            else {},
        }

    def test_hub_external_id_match_builds_the_native_library_tile(self):
        entrypoint, directory_calls, _, _ = load_entrypoint()
        xml = provider_metadata()
        ET.SubElement(xml[0], "Guid", id="tmdb://603")
        entrypoint._provider_xmls = lambda *args, **kwargs: [xml]
        self._legacy_agent_library_match(entrypoint)

        entrypoint.discover_hub("new-for-you")

        _, items, _ = directory_calls[0]
        self.assertEqual(len(items), 1)
        path, item, is_folder = items[0]
        self.assertEqual(
            path,
            "plugin://plugin.video.plexkodiconnect.movies/?plex_id=42&mode=play",
        )
        self.assertFalse(is_folder)
        self.assertNotEqual(item.get("IsPlayable"), "false")
        self.assertEqual(item["extraproperties"]["DBID"], "42")
        # The provider identity stays available for Watchlist actions
        self.assertEqual(item["extraproperties"]["ratingKey"], "abc123")
        self.assertEqual(
            item["extraproperties"]["plexguid"], "plex://movie/abc123"
        )

    def test_detail_external_id_match_opens_the_native_library_item(self):
        entrypoint, _, dialog_calls, _ = load_entrypoint()
        xml = provider_metadata()
        ET.SubElement(xml[0], "Guid", id="imdb://tt0133093")
        entrypoint._provider_xmls = lambda *args, **kwargs: [xml]
        self._legacy_agent_library_match(entrypoint)

        self.assertTrue(entrypoint.discover_detail("abc123"))

        item = dialog_calls[0]
        self.assertEqual(
            item["file"],
            "plugin://plugin.video.plexkodiconnect.movies/?plex_id=42&mode=play",
        )
        self.assertNotEqual(item.get("IsPlayable"), "false")
        self.assertEqual(item["extraproperties"]["DBID"], "42")
        self.assertEqual(item["extraproperties"]["ratingKey"], "abc123")
        self.assertEqual(
            item["extraproperties"]["plexguid"], "plex://movie/abc123"
        )


if __name__ == "__main__":
    unittest.main()
