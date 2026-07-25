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

    class API(object):
        def __init__(self, xml):
            self.xml = xml
            self.plex_type = xml.get("type")

    mass_api_calls = []

    def mass_api(xml, check_by_guid=False):
        mass_api_calls.append((list(xml), check_by_guid))
        if check_by_guid:
            return []
        return [API(child) for child in xml]

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
            @staticmethod
            def get_xml(url, cache_seconds):
                calls.append(("get", url, cache_seconds))
                return cached

            @staticmethod
            def put_xml(url, xmls):
                raise AssertionError("a cache hit must not be written or fetched")

        entrypoint.discover_cache = Cache()

        xmls = entrypoint._provider_xmls(
            "https://discover.provider.plex.tv/hubs/sections/home/new-for-you",
            ("discover.provider.plex.tv",),
            120,
        )

        self.assertEqual(xmls, cached)
        self.assertEqual(calls[0][0], "get")
        self.assertEqual(calls[0][2], 120)

    def test_provider_cache_miss_falls_back_to_network_then_stores_response(self):
        entrypoint, _, _, _ = load_entrypoint()
        stored = []

        class Cache(object):
            @staticmethod
            def get_xml(url, cache_seconds):
                return None

            @staticmethod
            def put_xml(url, xmls):
                stored.append((url, list(xmls)))
                return True

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
            120,
        )

        self.assertEqual(len(downloader.calls), 1)
        self.assertEqual(xmls[0][0].get("ratingKey"), "network")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0][1][0][0].get("ratingKey"), "network")

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

        def mass_api(metadata, check_by_guid=False):
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

        def mass_api(metadata, check_by_guid=False):
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


if __name__ == "__main__":
    unittest.main()
